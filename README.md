# Cruzamento Vegetação Secundária × APP/RL/AUR

Scripts para cruzar espacialmente o mapa de **vegetação secundária** do INPE
com as camadas temáticas de **APP** (Área de Preservação Permanente),
**RL** (Reserva Legal) e **AUR** (Área de Uso Restrito) dos imóveis
selecionados pela [análise de conformidade SICAR × INCRA](https://github.com/Geconsult-code/analise_conformidade_sicar-incra),
calculando a interseção geométrica real e a área geodésica (elipsoide
GRS80) de cada fragmento, por unidade da federação e por bioma.

Este repositório é a segunda etapa de um processo de duas etapas: primeiro
roda-se a análise de conformidade SICAR × INCRA (repositório
`analise_conformidade_sicar-incra`), que seleciona os imóveis analisados e
não analisados por UF; depois, com esses imóveis já selecionados, roda-se
este repositório para cruzá-los com a vegetação secundária.

## Instalação

```bash
conda create -n vegsec python=3.11 geopandas
conda activate vegsec
pip install -r requirements.txt
```

`relatorio_vegsec_selecionados.py` e `corrigir_geometrias_vegsec.py` usam
adicionalmente as bindings Python do GDAL (pacote `osgeo`). A forma mais
simples de obtê-las é `conda install -c conda-forge gdal` — ou rodar esses
dois scripts pelo interpretador Python embutido no QGIS, que já as inclui.
`cruzar_vegsec_selecionados.py` não depende de `osgeo` (usa apenas
GeoPandas/pyogrio), justamente para poder rodar em ambientes mais simples.

## Uso

Os três scripts principais formam um pipeline sequencial e cada um é
resiliente/retomável: se a execução for interrompida (fechar o terminal,
travamento da máquina, etc.), basta rodar o mesmo comando de novo — cada um
guarda seu progresso em um arquivo `_progresso_*.json` e continua exatamente
de onde parou, sem reprocessar nem duplicar nada já concluído.

### 1. Cruzamento espacial

```bash
python cruzar_vegsec_selecionados.py
```

Lê os imóveis selecionados nos geopackages de conformidade
(`UF_Conformidade_Imoveis_Nao_Analisados` e
`UF_Conformidade_Imoveis_Analisados`, gerados pelo repositório de
conformidade), cruza cada camada temática (APP/RL/AUR) com a vegetação
secundária do bioma correspondente e grava o resultado em:

```
Vegetacao_Secundaria/VS_Imoveis_Selecionados_Nao_Analisados.gpkg
Vegetacao_Secundaria/VS_Imoveis_Selecionados_Analisados.gpkg
```

cada um com até três camadas (`VS_APP_<categoria>`, `VS_RL_<categoria>`,
`VS_AUR_<categoria>`), contendo a geometria da interseção real entre o
polígono temático e o polígono de vegetação secundária, com a área
recalculada em hectares (geodésica, GRS80).

### 2. Relatório de consistência

```bash
python relatorio_vegsec_selecionados.py
```

Gera, para cada geopackage de saída, um JSON de mesmo nome
(`VS_Imoveis_Selecionados_<categoria>.json`) com estatísticas por camada:
número de polígonos, número de geometrias inválidas, número de feições sem
geometria, área total (ha) e área agregada por UF e por bioma.

### 3. Correção de geometrias inválidas (se necessário)

```bash
python corrigir_geometrias_vegsec.py
```

Só é preciso rodar se o relatório do passo 2 acusar
`num_geometrias_invalidas > 0` em alguma camada. Lê a lista de camadas
afetadas diretamente dos JSONs de relatório, repara cada geometria inválida
com `shapely.make_valid()` (fallback `buffer(0)`) e regrava só a geometria
da(s) feição(ões) afetada(s) — nunca adiciona nem remove linhas. Depois de
corrigir, rode o passo 2 de novo para confirmar que `num_geometrias_invalidas`
zerou em todas as camadas.

### Utilitário auxiliar

`dissolver_car_total.py` — dissolve/agrega camadas do CAR por critérios
próprios do projeto; não faz parte do pipeline principal de cruzamento, é
usado sob demanda.

## Validação

Os totais de área e contagem de polígonos por UF/bioma produzidos por
`cruzar_vegsec_selecionados.py` foram conferidos contra os números
históricos de execuções anteriores do mesmo cruzamento (mesma metodologia,
antes da reorganização deste repositório), com divergência compatível
apenas com a atualização da base de imóveis selecionados de entrada. O
relatório de consistência é a ferramenta oficial de auditoria de cada
execução: qualquer geometria inválida ou feição sem geometria fica
registrada por camada, por UF e por bioma.

## Estrutura

```
cruzamento_vegetacao-secundaria/
├── cruzar_vegsec_selecionados.py       # cruzamento espacial (etapa 1)
├── relatorio_vegsec_selecionados.py    # relatório de consistência (etapa 2)
├── corrigir_geometrias_vegsec.py       # correção de geometrias inválidas (etapa 3)
├── dissolver_car_total.py              # utilitário auxiliar
├── docs/
│   └── metodologia.md                  # metodologia científica detalhada
├── requirements.txt
├── pyproject.toml
├── LICENSE
├── CITATION.cff
└── .gitignore
```

## Autoria

Maurício Braga Meira — Geoconsult Ltda. (Consultoria especializada em
geoinformação).

## Como citar

Ver [`CITATION.cff`](CITATION.cff).
