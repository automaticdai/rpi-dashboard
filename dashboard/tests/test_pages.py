from conftest import make_pi


def test_overview_renders(make_client):
    c, _, _ = make_client({})
    with c:
        r = c.get("/")
    assert r.status_code == 200
    assert 'id="pis"' in r.text and "/static/app.js" in r.text


def test_detail_renders(make_client):
    c, _, _ = make_client({}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        r = c.get("/pi/A")
        assert r.status_code == 200 and 'id="charts"' in r.text and "h-A" in r.text
        assert c.get("/pi/Z").status_code == 404


def test_static_chartjs(make_client):
    c, _, _ = make_client({})
    with c:
        r = c.get("/static/chart.umd.min.js")
    assert r.status_code == 200 and len(r.content) > 100_000


def test_favicon_linked_on_every_page_and_served(make_client):
    c, _, _ = make_client({}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        for path in ("/", "/pi/A", "/bench", "/stress"):
            r = c.get(path)
            assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in r.text, path
        icon = c.get("/static/favicon.svg")
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")


def test_bench_page_renders_and_is_linked(make_client):
    c, _, _ = make_client({})
    with c:
        r = c.get("/bench")
        assert r.status_code == 200
        assert 'id="bench-table"' in r.text and "bench()" in r.text
        assert 'href="/bench"' in c.get("/").text


def test_stress_tab(make_client):
    c, _, _ = make_client({})
    with c:
        r = c.get("/stress")
        assert r.status_code == 200 and 'id="stress-form"' in r.text and 'id="live-charts"' in r.text
        assert 'href="/stress"' in c.get("/").text
        assert 'id="stress-form"' not in c.get("/bench").text


def test_overview_has_cards_list_switch(make_client):
    c, _, _ = make_client({})
    with c:
        r = c.get("/")
    assert 'data-layout="cards"' in r.text and 'data-layout="list"' in r.text and 'id="list-head"' in r.text
    assert 'data-layout="table"' in r.text


def test_events_tab(make_client):
    c, _, _ = make_client({})
    with c:
        r = c.get("/events")
        assert r.status_code == 200 and 'id="events-summary"' in r.text and 'id="timeline"' in r.text
        assert 'href="/events"' in c.get("/").text
