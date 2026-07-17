import os
import sys
import asyncio

# Configura o path para encontrar os modulos do src
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(current_dir))

from src.api.routers.admin.eval_api import EVAL_DATASET, _evaluate_single, _aggregate_results

async def main():
    print(f"🚀 Iniciando avaliacao local de {len(EVAL_DATASET)} perguntas...")
    results = []
    
    for item in EVAL_DATASET:
        print(f"\n📝 Avaliando [{item['id']}] | Rota esperada: {item['category']}")
        print(f"   Pergunta: '{item['question']}'")
        
        result = await _evaluate_single(item, session_id="eval_local")
        results.append(result)
        
        if result.error:
            print(f"   ❌ Erro: {result.error}")
        else:
            print(f"   ✅ Rota Detectada: {result.route_detected}")
            print(f"   📊 Metricas: Hit Rate={result.hit_rate} | MRR={result.mrr} | Faithfulness={result.faithfulness} | Relevancy={result.answer_relevancy}")
            print(f"   ⏱️  Latencia: {result.latency_ms}ms")
            print(f"   ✍️  Resposta: {result.answer[:140]}...")
            
    summary = _aggregate_results(results)
    print("\n" + "="*60)
    print("📈 RESULTADOS AGREGADOS:")
    print(f"   • Perguntas Completadas: {summary.completed}/{summary.total_questions}")
    print(f"   • Hit Rate Medio: {summary.avg_hit_rate:.3f}")
    print(f"   • MRR Medio: {summary.avg_mrr:.3f}")
    print(f"   • CRAG Score Medio: {summary.avg_crag:.3f}")
    print(f"   • Grounding (Faithfulness) Medio: {summary.avg_faithfulness:.3f}")
    print(f"   • Relevancia de Resposta Média: {summary.avg_relevancy:.3f}")
    print(f"   • Latencia Média: {summary.avg_latency_ms}ms")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
