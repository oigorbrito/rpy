# Avaliação offline do RAG

O repositório mantém um dataset sintético e determinístico para detectar
regressões no retrieval sem chamar Judit, Anthropic, OpenAI, Cohere ou qualquer
outro provider externo.

## Execução

```bash
python scripts/evaluate_rag.py --dataset tests/evaluation/dataset_v1.json --baseline tests/evaluation/baseline_v1.json
```

O mesmo contrato é exercitado pelo teste `tests/evaluation/test_evaluation_contract.py`.

## Métricas

- `required_movement_recall`: movimentos obrigatórios recuperados;
- `candidate_precision`: candidatos selecionados que pertencem ao conjunto relevante;
- `source_presence`: presença/ausência de candidatos conforme o caso;
- `leakage_free`: nenhum sentinel restrito aparece no contexto.

O caso sigiloso não executa retrieval: o contexto é vazio antes da seleção.

`baseline_v1.json` registra configuração, métricas e thresholds. Eles são o
baseline revisado deste dataset, não limiares universais. Mudança de modelo,
prompt, algoritmo, dataset ou configuração exige nova versão e revisão.

O gate é bloqueante para vazamento, ausência de fontes e perda de movimentos
obrigatórios. A precisão admite a margem documentada de 0,45, calibrada contra
o caso longo do baseline, que recupera candidatos adicionais legítimos.

O dataset contém somente dados sintéticos. Nenhuma credencial é necessária e
nenhum provider pago é chamado.
