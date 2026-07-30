import base64
import pytest
from unittest.mock import patch, MagicMock
from deployer import build_deployment_payload, extract_url_from_response, deploy_html_to_vercel

def test_build_payload_contains_html():
    html = "<html><body>Hola</body></html>"
    payload = build_deployment_payload(html, "test-project-123", "scalerics-demos")
    assert payload["name"] == "scalerics-demos"
    assert len(payload["files"]) == 1
    assert payload["files"][0]["file"] == "index.html"
    decoded = base64.b64decode(payload["files"][0]["data"]).decode()
    assert "Hola" in decoded

def test_extract_url_from_response_success():
    response_data = {"url": "scalerics-demos-abc123.vercel.app", "readyState": "READY"}
    url = extract_url_from_response(response_data)
    assert url == "https://scalerics-demos-abc123.vercel.app"

def test_extract_url_from_response_missing_url():
    with pytest.raises(KeyError):
        extract_url_from_response({"readyState": "ERROR"})

def test_deploy_retries_on_failure(tmp_path):
    html_path = tmp_path / "index.html"
    html_path.write_text("<html>test</html>", encoding="utf-8")

    call_count = 0
    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock = MagicMock()
        if call_count < 3:
            mock.raise_for_status.side_effect = Exception("Server error")
        else:
            mock.raise_for_status.return_value = None
            mock.json.return_value = {"url": "test-deploy.vercel.app"}
        return mock

    with patch("deployer.requests.post", side_effect=mock_post):
        url = deploy_html_to_vercel(str(html_path), 1, "fake-token", "scalerics-demos")
        assert url == "https://test-deploy.vercel.app"
        assert call_count == 3
