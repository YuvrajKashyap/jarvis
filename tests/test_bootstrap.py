from pathlib import Path

from fastapi.testclient import TestClient

from jarvis.bootstrap import BootstrapSettings, InMemorySecretStore, build_application


def test_composition_root_creates_local_state_without_external_connections(tmp_path: Path) -> None:
    settings = BootstrapSettings(
        data_directory=tmp_path / "data",
        memory_directory=tmp_path / "documents" / "JARVIS" / "Memory",
        file_roots=(tmp_path,),
        host="127.0.0.1",
        port=7331,
        allowed_hosts=("127.0.0.1", "localhost"),
        allowed_origins=("http://127.0.0.1:1420",),
        desktop_speech_enabled=False,
        model_prewarm_enabled=False,
    )
    secrets = InMemorySecretStore()

    application = build_application(settings=settings, secrets=secrets)

    with TestClient(application, base_url="http://127.0.0.1") as client:
        health = client.get("/v1/health")
    assert health.json() == {"status": "ok", "protocol_version": 1}
    assert (tmp_path / "data" / "jarvis.db").is_file()
    assert (tmp_path / "documents" / "JARVIS" / "Memory" / "memory.md").is_file()
    assert len(secrets.get("api-token") or "") >= 32
    assert [schema.name for schema in application.state.capabilities.tool_schemas()] == [
        "context.active_window",
        "system.health",
        "files.read_text",
        "files.write_text",
        "files.undo",
        "terminal.execute",
        "browser.inspect",
        "browser.navigate",
        "browser.click",
        "browser.fill",
        "windows.inspect",
        "windows.invoke",
        "windows.set_value",
        "memory.remember",
        "memory.undo_remember",
        "notifications.remind",
        "schedules.create",
        "schedules.undo_create",
    ]
    assert application.state.actions is not None
    assert application.state.browser is not None
    assert application.state.windows_automation is not None
    assert application.state.scheduler is not None
    assert application.state.backups is not None
    assert application.state.memory_retrieval is not None
    assert application.state.turn_context is not None


def test_composition_root_reuses_api_secret_across_restarts(tmp_path: Path) -> None:
    settings = BootstrapSettings(
        data_directory=tmp_path / "data",
        memory_directory=tmp_path / "memory",
        file_roots=(tmp_path,),
        desktop_speech_enabled=False,
        model_prewarm_enabled=False,
    )
    secrets = InMemorySecretStore()

    first = build_application(settings=settings, secrets=secrets)
    token = secrets.get("api-token")
    second = build_application(settings=settings, secrets=secrets)

    assert first is not second
    assert secrets.get("api-token") == token


def test_composition_root_adds_configured_tailnet_origin_for_phone_pairing(
    tmp_path: Path,
) -> None:
    settings = BootstrapSettings(
        data_directory=tmp_path / "data",
        memory_directory=tmp_path / "memory",
        file_roots=(tmp_path,),
        phone_base_url="https://yuvraj-omen.example.ts.net",
        desktop_speech_enabled=False,
        model_prewarm_enabled=False,
    )
    secrets = InMemorySecretStore()
    app = build_application(settings=settings, secrets=secrets)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/v1/pairing/offers",
            headers={"authorization": f"Bearer {secrets.get('api-token')}"},
        )

    assert response.status_code == 201
    assert response.json()["pairing_url"].startswith("https://yuvraj-omen.example.ts.net/#pair=")


def test_default_runtime_keeps_the_measured_primary_model_resident() -> None:
    settings = BootstrapSettings()

    assert settings.primary_model == "qwen3.5:4b-q4_K_M"
    assert settings.model_context_length == 4_096
    assert settings.model_prewarm_enabled is True
