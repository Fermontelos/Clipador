import clipador_api


def test_pacote_da_api_existe():
    assert "127.0.0.1" in clipador_api.__doc__
