/*
 * rpibench: a small CPU benchmark for rpi-dashboard. No dependencies beyond libc + pthreads.
 *
 *   rpibench int   <threads> <seconds>   integer score, Mops/s
 *   rpibench fp    <threads> <seconds>   floating-point score, MFLOPS (double precision)
 *   rpibench mem   <threads> <seconds>   RAM bandwidth, MB/s (STREAM-style triad, 8 MB arrays)
 *   rpibench burn  <threads>             busy loop on every thread until killed (stress)
 *
 * Prints one JSON line. Each thread repeats a fixed kernel until the time is up; the score is
 * total kernel iterations x the kernel's fixed operation count / elapsed time. Working sets are
 * a few KB so the CPU, not memory, is measured.
 *
 * Build (same flags on every Pi): cc -O2 -march=armv8-a -pthread -o rpibench rpibench.c
 */
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ---------- integer kernel ---------- */
#define CRC_BYTES 4096
#define IM 16
#define PRIME_FROM 100000u
#define PRIME_COUNT 64u
/* Fixed "operation" counts used for the score (an index, comparable across boards):
 * CRC: 4 ops/byte; matrix: 2 ops per multiply-add; primes: 2 ops per trial division. */
static uint64_t int_ops_per_iter;

static uint32_t crc_table[256];

static void crc_init(void) {
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crc_table[i] = c;
    }
}

static uint64_t prime_divisions(void) {
    uint64_t n = 0;
    for (uint32_t x = PRIME_FROM; x < PRIME_FROM + PRIME_COUNT; x++)
        for (uint32_t d = 2; d * d <= x; d++) { n++; if (x % d == 0) break; }
    return n;
}

typedef struct {
    uint8_t buf[CRC_BYTES];
    int32_t a[IM][IM], b[IM][IM], c[IM][IM];
} int_state;

static uint64_t int_kernel(int_state *s, uint32_t seed) {
    uint32_t crc = ~seed;
    for (int i = 0; i < CRC_BYTES; i++) crc = crc_table[(crc ^ s->buf[i]) & 0xFF] ^ (crc >> 8);
    for (int i = 0; i < IM; i++)
        for (int j = 0; j < IM; j++) {
            int32_t acc = 0;
            for (int k = 0; k < IM; k++) acc += s->a[i][k] * s->b[k][j];
            s->c[i][j] = acc ^ (int32_t)crc;
        }
    uint32_t primes = 0;
    for (uint32_t x = PRIME_FROM + (seed & 1); x < PRIME_FROM + PRIME_COUNT + (seed & 1); x++) {
        uint32_t d = 2, is_prime = 1;
        for (; d * d <= x; d++) if (x % d == 0) { is_prime = 0; break; }
        primes += is_prime;
    }
    s->buf[seed % CRC_BYTES] ^= (uint8_t)crc; /* feed results back so nothing is optimised away */
    return crc + (uint64_t)s->c[seed % IM][(seed >> 4) % IM] + primes;
}

/* ---------- floating-point kernel ---------- */
#define FM 32
#define FSQRT 1024
#define FP_FLOPS_PER_ITER (2.0 * FM * FM * FM + 3.0 * FSQRT)

typedef struct {
    double a[FM][FM], b[FM][FM], c[FM][FM];
    double v[FSQRT];
} fp_state;

static double fp_kernel(fp_state *s, uint32_t seed) {
    for (int i = 0; i < FM; i++)
        for (int j = 0; j < FM; j++) {
            double acc = 0.0;
            for (int k = 0; k < FM; k++) acc += s->a[i][k] * s->b[k][j];
            s->c[i][j] = acc;
        }
    double r = 0.0;
    for (int i = 0; i < FSQRT; i++) r += sqrt(s->v[i] + s->c[i % FM][(i / FM) % FM] * 1e-9) * 0.5;
    s->a[seed % FM][(seed >> 5) % FM] = 1.0 + r * 1e-12; /* feedback */
    return r;
}

/* ---------- memory bandwidth kernel ---------- */
/* Triad a[i] = b[i] + s*c[i] over arrays far bigger than any Pi's caches (8 MB each, 24 MB per
 * thread), so it measures RAM. Bytes counted per pass: 3 arrays x 8 MB (2 reads + 1 write). */
#define MEM_N (1u << 20) /* doubles per array = 8 MB */
#define MEM_BYTES_PER_ITER (3.0 * MEM_N * sizeof(double))

typedef struct {
    double *a, *b, *c;
} mem_state;

static double mem_kernel(mem_state *s, uint32_t seed) {
    const double k = 1.0 + (seed & 7) * 1e-9;
    double *restrict a = s->a, *restrict b = s->b, *restrict c = s->c;
    for (uint32_t i = 0; i < MEM_N; i++) a[i] = b[i] + k * c[i];
    return a[seed % MEM_N];
}

/* ---------- threading ---------- */
static volatile int stop_flag = 0;

typedef struct {
    int mode; /* 0 int, 1 fp, 2 mem */
    uint64_t iterations;
    double sink;
} worker;

static void *run_worker(void *arg) {
    worker *w = arg;
    uint32_t seed = (uint32_t)(uintptr_t)w;
    if (w->mode == 0) {
        int_state *s = malloc(sizeof *s);
        for (int i = 0; i < CRC_BYTES; i++) s->buf[i] = (uint8_t)(i * 31 + 7);
        for (int i = 0; i < IM; i++)
            for (int j = 0; j < IM; j++) { s->a[i][j] = i * 3 + j + 1; s->b[i][j] = i - j * 2 + 5; }
        uint64_t acc = 0;
        while (!stop_flag) { acc += int_kernel(s, seed++); w->iterations++; }
        w->sink = (double)acc;
        free(s);
    } else if (w->mode == 2) {
        mem_state s = {malloc(MEM_N * sizeof(double)), malloc(MEM_N * sizeof(double)), malloc(MEM_N * sizeof(double))};
        if (!s.a || !s.b || !s.c) { free(s.a); free(s.b); free(s.c); return NULL; }
        for (uint32_t i = 0; i < MEM_N; i++) { s.a[i] = 0; s.b[i] = 1.0 + i * 1e-9; s.c[i] = 2.0 - i * 1e-9; }
        double acc = 0.0;
        while (!stop_flag) { acc += mem_kernel(&s, seed++); w->iterations++; }
        w->sink = acc;
        free(s.a); free(s.b); free(s.c);
    } else {
        fp_state *s = malloc(sizeof *s);
        for (int i = 0; i < FM; i++)
            for (int j = 0; j < FM; j++) { s->a[i][j] = 1.0 + i * 1e-3; s->b[i][j] = 1.0 - j * 1e-3; }
        for (int i = 0; i < FSQRT; i++) s->v[i] = 1.0 + i;
        double acc = 0.0;
        while (!stop_flag) { acc += fp_kernel(s, seed++); w->iterations++; }
        w->sink = acc;
        free(s);
    }
    return NULL;
}

static double now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static int usage(void) {
    fprintf(stderr, "usage: rpibench int|fp|mem <threads> <seconds>  |  rpibench burn <threads>\n");
    return 2;
}

int main(int argc, char **argv) {
    if (argc < 3) return usage();
    int burn = strcmp(argv[1], "burn") == 0;
    int mode = strcmp(argv[1], "fp") == 0 ? 1 : strcmp(argv[1], "mem") == 0 ? 2 : 0;
    if (!burn && strcmp(argv[1], "int") != 0 && strcmp(argv[1], "fp") != 0 && strcmp(argv[1], "mem") != 0)
        return usage();
    int threads = atoi(argv[2]);
    double secs = burn ? 0 : (argc > 3 ? atof(argv[3]) : 5.0);
    if (threads < 1 || threads > 256 || (!burn && (secs <= 0 || secs > 600))) return usage();

    crc_init();
    int_ops_per_iter = 4ull * CRC_BYTES + 2ull * IM * IM * IM + 2ull * prime_divisions();

    worker *ws = calloc(threads, sizeof *ws);
    pthread_t *ts = calloc(threads, sizeof *ts);
    double t0 = now();
    for (int i = 0; i < threads; i++) {
        ws[i].mode = mode;
        pthread_create(&ts[i], NULL, run_worker, &ws[i]);
    }
    if (burn) {
        for (int i = 0; i < threads; i++) pthread_join(ts[i], NULL); /* never returns; killed */
        return 0;
    }
    struct timespec nap = {(time_t)secs, (long)((secs - (time_t)secs) * 1e9)};
    nanosleep(&nap, NULL);
    stop_flag = 1;
    uint64_t total = 0;
    double sink = 0;
    for (int i = 0; i < threads; i++) {
        pthread_join(ts[i], NULL);
        total += ws[i].iterations;
        sink += ws[i].sink;
    }
    double elapsed = now() - t0;
    static const char *names[] = {"int", "fp", "mem"}, *units[] = {"Mops/s", "MFLOPS", "MB/s"};
    double per_iter = mode == 0 ? (double)int_ops_per_iter : mode == 1 ? FP_FLOPS_PER_ITER : MEM_BYTES_PER_ITER;
    printf("{\"test\": \"%s\", \"threads\": %d, \"seconds\": %.3f, \"score\": %.1f, \"unit\": \"%s\", "
           "\"iterations\": %llu, \"check\": %.0f}\n",
           names[mode], threads, elapsed, (double)total * per_iter / elapsed / 1e6, units[mode],
           (unsigned long long)total, fmod(sink, 1e9));
    free(ws);
    free(ts);
    return 0;
}
