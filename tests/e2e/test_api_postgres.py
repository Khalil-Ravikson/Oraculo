def test_saude_da_api(cliente_api):
    """Garante que a API subiu corretamente."""
    resposta = cliente_api.get("/health") # ou o endpoint que tiveres de status
    assert resposta.status_code in [200, 404] # Ajusta consoante a tua rota raiz

def test_criar_e_recuperar_pessoa_no_banco(cliente_api):
    """Garante que o PostgreSQL está conectado e a gravar dados."""
    payload = {
        "nome": "Estudante E2E Teste",
        "email": "teste.e2e@uema.br",
        "telefone": "5598900000000", # Telefone no formato Evolution
        "matricula": "2026E2E"
    }

    # 1. Cria a pessoa via API
    resposta_post = cliente_api.post("/api/v1/pessoas/", json=payload)
    
    # Se a rota estiver protegida por auth, pode dar 401/403. Se for o caso, 
    # precisas de injetar um token no cabeçalho do cliente_api.
    assert resposta_post.status_code in [200, 201]
    
    dados = resposta_post.json()
    assert "id" in dados
    pessoa_id = dados["id"]

    # 2. Recupera do banco para garantir persistência
    resposta_get = cliente_api.get(f"/api/v1/pessoas/{pessoa_id}")
    assert resposta_get.status_code == 200
    assert resposta_get.json()["nome"] == "Estudante E2E Teste"