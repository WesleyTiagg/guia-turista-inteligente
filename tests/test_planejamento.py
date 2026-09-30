from planejamento import obter_guia_destino_com_diagnostico


def test_fallback_usa_o_destino_e_nao_uma_tabela_fixa():
    guia, diagnostico = obter_guia_destino_com_diagnostico(
        "Salvador - BA",
        origem="Recife - PE",
        clima={"temperatura": "27.0 °C", "umidade": "67%", "vento": "18.4 km/h"},
        percurso={"distancia": "802.4 km", "tempo": "10h 50min de carro"},
    )

    assert diagnostico["fallback_utilizado"] is True
    assert "Salvador" in guia
    assert "🏛️ Atrações" in guia
    assert "principais pontos turísticos" in guia
    assert "culinária local" in guia
    assert "Pelourinho" not in guia
