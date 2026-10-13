def test_root_endpoint_serves_dashboard(test_client):
    response = test_client.get("/")
    assert response.status_code == 200
    assert "<!DOCTYPE html>" in response.text
    # The dashboard was rebranded from "AIOPS COMMAND CENTER" to Sentinelas
    # (see d4d6a87), so assert on the header brand and the primary action
    # button rather than a hardcoded legacy title.
    assert ">Sentinelas<" in response.text
    assert 'id="btnInjectChaos"' in response.text

def test_api_meta_endpoint(test_client):
    response = test_client.get("/api")
    assert response.status_code == 200
    data = response.json()
    assert "platform" in data
    assert data["docs_url"] == "/docs"

def test_health_check_endpoint(test_client):
    response = test_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["mode"] == "autonomous"
