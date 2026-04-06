import pytest
from src.application.graph.nodes import retrieve_node, generate_node
from src.application.graph.state import GraphState # O teu estado do LangGraph

class TestNodesLangGraph:
    """Testa os nós do grafo de forma isolada usando Clean Architecture."""

    def test_retrieve_node_com_sucesso(self, fake_vector_store):
        # 1. Arrange: Prepara o estado inicial e o mock
        estado_inicial: GraphState = {
            "pergunta": "Quais as regras do RU?",
            "contexto": "",
            "rota": "edital",
            "tentativas": 0
        }
        
        # Injetamos o que o VectorDB deveria retornar
        fake_vector_store.documentos_mock = [
            {"texto": "O RU não permite entrada sem carteirinha.", "score": 0.95}
        ]

        # 2. Act: Executa o nó injetando a dependência falsa
        # Assumindo que o teu retrieve_node aceita o vector_store como injeção ou o puxa de um container
        novo_estado = retrieve_node(estado_inicial, vector_store=fake_vector_store)

        # 3. Assert: O estado do grafo foi atualizado corretamente?
        assert novo_estado["contexto"] != ""
        assert "carteirinha" in novo_estado["contexto"]
        assert novo_estado["pergunta"] == estado_inicial["pergunta"] # Imutabilidade de dados base

    def test_generate_node_utiliza_contexto(self, fake_llm):
        # 1. Arrange
        estado_com_contexto: GraphState = {
            "pergunta": "Como acesso o RU?",
            "contexto": "Regra: usar carteirinha digital.",
            "rota": "geral",
            "resposta_final": ""
        }
        fake_llm.respostas_programadas["default"] = "Para acessar o RU, use a carteirinha digital."

        # 2. Act
        novo_estado = generate_node(estado_com_contexto, llm_provider=fake_llm)

        # 3. Assert
        assert novo_estado["resposta_final"] == "Para acessar o RU, use a carteirinha digital."
        # Garante que o prompt montado e enviado à LLM continha o contexto
        assert "usar carteirinha digital" in fake_llm.ultima_chamada