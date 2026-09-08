# Cruzamento Vegetação Secundária × APP/RL/AUR

Scripts para cruzar espacialmente o mapa de **vegetação secundária** do INPE
com as camadas temáticas de **APP** (Área de Preservação Permanente),
**RL** (Reserva Legal) e **AUR** (Área de Uso Restrito) dos imóveis
selecionados pela [análise de conformidade SICAR × INCRA](https://github.com/Geconsult-code/analise_conformidade_sicar-incra),
calculando a interseção geométrica real e a área geodésica (elipsoide
GRS80) de cada fragmento, por unidade da federação e por bioma.

Este repositório é a segunda etapa de um processo de duas etapas: primeiro
roda-se o pipeline de produção de seis passos do repositório
`analise_conformidade_sicar-incra`, que categoriza e seleciona os imóveis
**Habilitados**, **Analisados** (com pendência de notificação) e **Não
Analisados** por UF, recortando também as camadas temáticas APP/RL/AUR de
cada um; depois, com esses imóveis já selecionados, roda-se este
repositório para cruzá-los com a vegetação secundária.

## Instalação

```bash
conda create -n vegsec python=3.11 geopandas
conda activate vegsec
pip install -r requirements.txt
```

`3_analise_consistencia_VS.py` e `4_validacao_resultados_VS.py` usam
adicionalmente as bindings Python do GDAL (pacote `osgeo`). A forma mais
simples de obtê-las é `conda install -c conda-forge gdal` — ou rodar esses
dois scripts pelo interpretador Python embutido no QGIS, que já as inclui.
`1_processar_dados_VS.py` e `2_cruzamento_espacial_VS.py` não dependem de
`osgeo` (usam apenas GeoPandas/pyogrio), justamente para poder rodar em
ambientes mais simples.

## Uso

Pipeline de produção (scripts numerados), sequencial e cada passo
resiliente/retomável: se a execução for interrompida (fechar o terminal,
travamento da máquina, etc.), basta rodar o mesmo comando de novo — cada um
guarda seu progresso em um arquivo `_progresso_*.json` e continua exatamente
de onde parou, sem reprocessar nem duplicar nada já concluído.

### 1. Processamento da vegetação secundária por UF

```bash
python 1_processar_dados_VS.py
```

Recorta a vegetação secundária nacional do INPE (`Vegetacao_Secundaria_2022.gpkg`,
6 camadas por bioma) para o bounding box de cada UF (união dos limites dos
imóveis já processados pelo repositório `analise_conformidade_sicar-incra`),
juntando os biomas num único arquivo/camada por estado — com `bioma`, `classe`
e `ano` como colunas de atributo — e gravando-o **no mesmo diretório dos
outros dados do repositório de análise_conformidade_sicar-incra**:

```
Analise_Conformidade/dados_saída_<UF>/<UF>_geopackage/<UF>_Vegetacao_Secundaria.gpkg
   layer: VS_<UF>
```

Esse recorte prévio por UF é consumido pelo passo 2, evitando reler os 6
biomas inteiros do INPE a cada categoria/tipo de imóvel.

### 2. Cruzamento espacial

```bash
python 2_cruzamento_espacial_VS.py
```

Lê os imóveis selecionados de cada categoria — `<UF>_Imoveis_Privados_Habilitados`,
`<UF>_Conformidade_Imoveis_Analisados` e `<UF>_Conformidade_Imoveis_Nao_Analisados`,
gerados pelo repositório de conformidade —, cruza cada camada temática
(APP/RL/AUR) já recortada para os imóveis selecionados com a vegetação
secundária pré-recortada da UF (passo 1) e grava o resultado em:

```
Vegetacao_Secundaria/VS_Imoveis_Selecionados_Habilitados.gpkg
Vegetacao_Secundaria/VS_Imoveis_Selecionados_Analisados.gpkg
Vegetacao_Secundaria/VS_Imoveis_Selecionados_Nao_Analisados.gpkg
```

cada um com até três camadas (`VS_APP_<categoria>`, `VS_RL_<categoria>`,
`VS_AUR_<categoria>`), contendo a geometria da interseção real entre o
polígono temático e o polígono de vegetação secundária, com a área
recalculada em hectares (geodésica, GRS80).

### 3. Análise de consistência

```bash
python 3_analise_consistencia_VS.py
```

Gera, para cada geopackage de saída do passo 2, um JSON de mesmo nome
(`VS_Imoveis_Selecionados_<categoria>.json`) com estatísticas por camada:
número de polígonos, número de geometrias inválidas, número de feições sem
geometria, área total (ha) e área agregada por UF e por bioma.

### 4. Validação dos resultados (correção de geometrias + verificação final)

```bash
python 4_validacao_resultados_VS.py
```

Só tem efeito se o relatório do passo 3 acusar `num_geometrias_invalidas > 0`
em alguma camada — nesse caso corrige cada geometria inválida com
`shapely.make_valid()` (fallback `buffer(0)`), regravando só a geometria
da(s) feição(ões) afetada(s) (nunca adiciona nem remove linhas), e em
seguida reescaneia automaticamente todas as camadas corrigidas para
confirmar que `num_geometrias_invalidas` zerou em todas elas.

### Utilitário auxiliar

`dissolver_car_total.py` foi removido deste repositório (não é mais
necessário no pipeline).

## Validação

Os totais de área e contagem de polígonos por UF/bioma produzidos por
`2_cruzamento_espacial_VS.py` foram conferidos contra os números
históricos de execuções anteriores do mesmo cruzamento (mesma metodologia,
antes da reorganização deste repositório), com divergência compatível
apenas com a atualização da base de imóveis selecionados de entrada. O
relatório de consistência é a ferramenta oficial de auditoria de cada
execução: qualquer geometria inválida ou feição sem geometria fica
registrada por camada, por UF e por bioma.

## Estrutura

```
cruzamento_vegetacao-secundaria/
├── 1_processar_dados_VS.py             # recorte da VS por UF (passo 1)
├── 2_cruzamento_espacial_VS.py         # cruzamento espacial (passo 2)
├── 3_analise_consistencia_VS.py        # relatório de consistência (passo 3)
├── 4_validacao_resultados_VS.py        # correção de geometrias + verificação (passo 4)
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
