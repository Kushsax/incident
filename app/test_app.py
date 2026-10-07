import main


def setup_function():
    main.chaos.reset()


def test_work_ok():
    c = main.app.test_client()
    assert c.get("/work").status_code == 200


def test_fail_rate_forces_500():
    c = main.app.test_client()
    c.post("/chaos/fail-rate", json={"value": 1.0})
    assert c.get("/work").status_code == 500


def test_reset_restores_service():
    c = main.app.test_client()
    c.post("/chaos/fail-rate", json={"value": 1.0})
    c.post("/chaos/reset")
    assert c.get("/work").status_code == 200


def test_fail_rate_is_clamped():
    c = main.app.test_client()
    r = c.post("/chaos/fail-rate", json={"value": 5})
    assert r.get_json()["fail_rate"] == 1.0


def test_metrics_exposed_and_count_errors():
    c = main.app.test_client()
    c.post("/chaos/fail-rate", json={"value": 1.0})
    c.get("/work")
    text = c.get("/metrics").get_data(as_text=True)
    assert "http_requests_total" in text
    assert 'status="500"' in text
    assert "http_request_duration_seconds_bucket" in text
