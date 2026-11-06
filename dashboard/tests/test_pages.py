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
