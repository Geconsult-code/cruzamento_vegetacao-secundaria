# Metodologia — Cruzamento Vegetação Secundária × APP/RL/AUR

## 1. Objetivo

Quantificar, para os imóveis rurais já categorizados e selecionados pela
análise de conformidade SICAR × INCRA (repositório
`analise_conformidade_sicar-incra`, nas três categorias Habilitados,
Analisados e Não Analisados), quanto de cada camada temática de interesse
ambiental (APP, Reserva Legal e Área de Uso Restrito) é efetivamente
coberto por vegetação secundária, segundo o mapeamento do INPE. O
resultado é usado como insumo para análises de MRV (monitoramento, relato
e verificação) de regeneração/desmatamento em áreas legalmente protegidas
dentro do imóvel.

## 2. Dados de entrada

- **Imóveis selecionados**: camadas temáticas `CAR_<UF>_{APP,RL,AUR}_Selecionados_<categoria>`
  do repositório de conformidade SICAR × INCRA (passo 5 do pipeline
  numerado daquele repositório), por UF e por categoria — Habilitados
  (`<UF>_Imoveis_Privados_Habilitados.gpkg`), Analisados e Não Analisados
  (`<UF>_Conformidade_Imoveis_<categoria>.gpkg`).
- **Vegetação secundária**: mapeamento do INPE (`Vegetacao_Secundaria_2022.gpkg`),
  organizado por bioma (Amazônia, Cerrado, Caatinga, Mata Atlântica,
  Pampa, Pantanal).

## 3. Recorte prévio por UF (passo 1)

Antes do cruzamento propriamente dito, `1_processar_dados_VS.py` recorta a
vegetação secundária nacional (6 camadas por bioma) para cada UF, por
bounding box — não por limite administrativo exato, mesmo padrão já
validado no cruzamento original: o bbox de cada UF é a união dos limites
(`total_bounds`) das três camadas de imóveis já processadas pelo
repositório de conformidade para aquele estado, evitando depender de uma
malha de limites estaduais à parte. Os biomas que intersectam esse bbox
são concatenados num único arquivo/camada por UF
(`<UF>_Vegetacao_Secundaria.gpkg`, layer `VS_<UF>`), mantendo `bioma`,
`classe` e `ano` como colunas de atributo — gravado no mesmo diretório de
saída do repositório `analise_conformidade_sicar-incra`
(`Analise_Conformidade\dados_saída_<UF>\<UF>_geopackage\`).

Esse recorte prévio é executado uma única vez por UF (não por UF × 3
categorias × 3 temas como antes) e consumido pelo passo 2, reduzindo
drasticamente a quantidade de leituras da base nacional de vegetação.

## 4. Desenho do cruzamento espacial (passo 2)

Para cada combinação **UF × categoria (Habilitados/Analisados/Não
Analisados) × tipo (APP/RL/AUR)**:

1. **Carregamento único por item** — a camada temática da UF inteira é
   carregada uma única vez em memória e cacheada por item
   (`uf::categoria::tipo`); a vegetação secundária já pré-recortada da UF
   pelo passo 1 é carregada uma única vez por UF e cacheada
   separadamente, reaproveitada entre as três categorias/tipos do mesmo
   estado (ela não depende de categoria nem de tipo). Esse desenho —
   descarregar a vegetação do disco uma vez por item, não por bloco de
   feições — é a evolução do desenho definitivo já adotado no cruzamento
   original: uma primeira versão que recarregava a vegetação via bounding
   box a cada bloco de ~5.000 feições causava leituras quase completas e
   repetidas de camadas de bioma muito grandes em UFs extensas/
   heterogêneas (as FIDs não são espacialmente agrupadas), levando a
   consumo de memória crescente e travamento da máquina em execução real
   (caso observado em Minas Gerais). O passo 1 leva essa mesma lição um
   passo adiante, pré-computando o recorte por UF uma única vez, antes de
   qualquer item entrar no laço de cruzamento.
2. **Limpeza prévia de geometrias** — antes do cruzamento (tanto no passo
   1 quanto no passo 2), tanto a camada temática quanto a vegetação
   secundária passam por reparo de geometria: `shapely.make_valid()`, com
   fallback `buffer(0)` quando `make_valid()` falha; geometrias que
   continuam inválidas ou vazias após ambas as tentativas são descartadas
   do cruzamento (com contagem registrada). Esse é o mesmo padrão de
   reparo já validado no repositório de conformidade para o caso
   patológico do Amazonas (`IllegalArgumentException: mixed-dimension`),
   reaplicado aqui por segurança.
3. **Interseção geométrica** — junção espacial (`sjoin`, predicado
   `intersects`) seguida da interseção geométrica real par a par
   (`geometry.intersection`) entre cada polígono temático e cada polígono
   de vegetação secundária sobreposto, preservando apenas a geometria
   resultante da sobreposição (não o polígono temático inteiro).
4. **Processamento em blocos (chunks)** — o passo 3 acima, já com os
   dados carregados em memória (passo 1 deste item), é executado em
   blocos posicionais de tamanho fixo (`CHUNK`) sobre a camada temática,
   cada bloco escrito incrementalmente na camada de saída (GeoPandas/
   pyogrio, modo append).
5. **Área geodésica** — a área de cada fragmento de interseção é
   recalculada em hectares usando geodésia real sobre o elipsoide GRS80
   (`pyproj.Geod`, `ellps="GRS80"`), não a área planar de uma projeção.
6. **Padronização geométrica** — toda geometria de saída é forçada para
   MultiPolygon, garantindo tipo estável entre blocos concatenados na
   mesma camada.

Para a categoria Habilitados, a camada temática de origem não possui a
coluna `selecao_final` (esses imóveis não passam pela classificação de
coerência do pipeline de conformidade — por definição já não têm
pendência), então essa coluna simplesmente não é gravada nos registros de
saída dessa categoria; `des_condic`/`selecao_final` são gravados apenas
quando presentes na origem.

## 5. Execução resiliente/retomável

O cruzamento é organizado como uma lista de trabalho (`worklist`) de itens
UF × categoria × tipo. Um arquivo de progresso (`_progresso_*.json`) guarda,
por item, o índice do próximo bloco a processar. Cada chamada de `rodada()`
processa itens dentro de um orçamento de tempo (`BUDGET_S`) e retorna
`False` se ainda restar trabalho — o laço `while not rodada(): pass` no
`__main__` simplesmente chama de novo até `True`. Ao concluir um item, o
cache da camada temática é liberado e `gc.collect()` é chamado
explicitamente para garantir a liberação de handles do GDAL/OGR mantidos
pelo interpretador Python (relevante em execuções longas ou via ponte
QGIS, cujo interpretador Python persiste entre chamadas); o cache da
vegetação por UF só é liberado ao trocar de estado.

O passo 1 (recorte por UF) é resiliente no nível de UF: um arquivo de
progresso guarda quais estados já foram concluídos, e rodar de novo pula
os já prontos (a menos que a UF esteja em `REFAZER`).

## 6. Análise de consistência (passo 3)

`3_analise_consistencia_VS.py` audita cada geopackage de saída do passo 2,
produzindo um JSON (mesmo nome do `.gpkg`) com, por camada: número total de
polígonos, número de geometrias inválidas (varredura completa, em blocos,
via `IsValid()`), número de feições sem geometria, área total (ha) e área
agregada por UF e por bioma (via SQL agregado direto sobre o GeoPackage/
SQLite, usando o atributo `area_ha` já calculado no cruzamento — não há
necessidade de reprojeção geométrica para essa agregação).

## 7. Validação final dos resultados (passo 4)

Caso o relatório do passo 3 acuse geometrias inválidas remanescentes na
saída (o que não é esperado no fluxo normal, já que a limpeza prévia do
passo 1/2 deveria eliminá-las antes da interseção, mas pode ocorrer por
reintrodução de invalidade durante a própria operação de interseção
geométrica), `4_validacao_resultados_VS.py` lê a lista de camadas afetadas
diretamente dos JSONs de relatório do passo 3 e regrava, feição a feição,
apenas a geometria das que estão inválidas — usando o mesmo reparo
`make_valid()`/`buffer(0)` — sem alterar contagem de feições nem o
atributo `area_ha` (o reparo de uma invalidade típica é uma correção
subpixel, sem efeito prático na área já calculada). A correção também é
resiliente/retomável, no mesmo padrão de orçamento de tempo por chamada.
Ao final da correção, o próprio script roda automaticamente uma
verificação (reescaneando as camadas corrigidas) e imprime a confirmação
de que `num_geometrias_invalidas` zerou em todas elas — dispensando uma
segunda rodada manual do passo 3 só para conferir.

## 8. Limitações conhecidas

- O atributo `area_ha` de feições corrigidas pelo passo 4 não é
  recalculado após o reparo geométrico — decisão deliberada, dado que o
  reparo típico altera a geometria em escala subpixel.
- O recorte por UF (passo 1) é por bounding box, não por limite
  administrativo exato — pode incluir uma faixa estreita de vegetação
  secundária de estados vizinhos próxima à fronteira, sem efeito prático
  no cruzamento do passo 2 (que só grava interseções reais com a
  temática daquela UF).
