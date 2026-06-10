def test_app_importable():
    from app.main import app

    assert app.title == "InsideDCPulse"
