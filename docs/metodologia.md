# Metodologia — Cruzamento Vegetação Secundária × APP/RL/AUR

## 1. Objetivo

Quantificar, para os imóveis rurais já selecionados pela análise de
conformidade SICAR × INCRA (repositório `analise_conformidade_sicar-incra`),
quanto de cada camada temática de interesse ambiental (APP, Reserva Legal e
Área de Uso Restrito) é efetivamente coberto por vegetação secundária,
segundo o mapeamento do INPE. O resultado é usado como insumo para análises
de MRV (monitoramento, relato e verificação) de regeneração/desmatamento em
áreas legalmente protegidas dentro do imóvel.

## 2. Dados de entrada

- **Imóveis selecionados**: camadas `UF_Conformidade_Imoveis_Nao_Analisados`
  e `UF_Conformidade_Imoveis_Analisados`, produzidas pelo repositório de
  conformidade SICAR × INCRA, uma por UF, já com as camadas temáticas
  (APP/RL/AUR) do CAR recortadas ao universo de imóveis selecionado por
  aquela análise.
- **Vegetação secundária**: mapeamento do INPE (`Vegetacao_Secundaria_2022.gpkg`),
  organizado por bioma (Amazônia, Cerrado, Caatinga, Mata Atlântica,
  Pampa, Pantanal).

## 3. Correspondência UF → bioma

Cada UF é associada ao(s) bioma(s) que a compõem (uma UF pode abranger mais
de um bioma, ex. Maranhão = Amazônia + Cerrado). O script cruza cada camada
temática da UF apenas contra o(s) bioma(s) correspondentes, evitando
processar a base nacional inteira de vegetação a cada UF.

## 4. Desenho do cruzamento espacial

Para cada combinação **UF × categoria (Analisados/Não Analisados) ×
tipo (APP/RL/AUR)**:

1. **Carregamento único por item** — a camada temática da UF inteira e a
   vegetação secundária de cada bioma relevante (filtrada pelo bounding box
   da camada temática) são carregadas **uma única vez** em memória e
   cacheadas (`_CACHE`) para todo o processamento daquele item, evitando
   releitura repetida da base de vegetação a cada bloco de feições
   processado. Esse é o desenho definitivo do script, adotado após um
   desenho anterior — que recarregava a vegetação via bounding box a cada
   bloco de ~5.000 feições — causar leituras quase completas e repetidas de
   camadas de bioma muito grandes em UFs extensas/heterogêneas (as FIDs não
   são espacialmente agrupadas), levando a um consumo de memória crescente
   e travamento da máquina em execução real (caso observado em Minas
   Gerais).
2. **Limpeza prévia de geometrias** — antes do cruzamento, tanto a camada
   temática quanto a vegetação secundária passam por reparo de geometria:
   `shapely.make_valid()`, com fallback `buffer(0)` quando `make_valid()`
   falha; geometrias que continuam inválidas ou vazias após ambas as
   tentativas são descartadas do cruzamento (com contagem registrada). Esse
   é o mesmo padrão de reparo já validado no repositório de conformidade
   para o caso patológico do Amazonas (`IllegalArgumentException:
   mixed-dimension`), reaplicado aqui por segurança.
3. **Interseção geométrica** — junção espacial (`sjoin`, predicado
   `intersects`) seguida da interseção geométrica real par a par
   (`geometry.intersection`) entre cada polígono temático e cada polígono
   de vegetação secundária sobreposto, preservando apenas a geometria
   resultante da sobreposição (não o polígono temático inteiro).
4. **Processamento em blocos (chunks)** — o passo 3, já com os dados
   carregados em memória (passo 1), é executado em blocos posicionais de
   tamanho fixo (`CHUNK`) sobre a camada temática, cada bloco escrito
   incrementalmente na camada de saída (GeoPandas/pyogrio, modo append).
5. **Área geodésica** — a área de cada fragmento de interseção é recalculada
   em hectares usando geodésia real sobre o elipsoide GRS80 (`pyproj.Geod`,
   `ellps="GRS80"`), não a área planar de uma projeção.
6. **Padronização geométrica** — toda geometria de saída é forçada para
   MultiPolygon, garantindo tipo estável entre blocos concatenados na mesma
   camada.

## 5. Execução resiliente/retomável

O cruzamento é organizado como uma lista de trabalho (`worklist`) de itens
UF × categoria × tipo. Um arquivo de progresso (`_progresso_*.json`) guarda,
por item, o índice do próximo bloco a processar. Cada chamada de `rodada()`
processa itens dentro de um orçamento de tempo (`BUDGET_S`) e retorna
`False` se ainda restar trabalho — o laço `while not rodada(): pass` no
`__main__` simplesmente chama de novo até `True`. Ao concluir um item, o
cache do passo 1 é liberado e `gc.collect()` é chamado explicitamente para
garantir a liberação de handles do GDAL/OGR mantidos pelo interpretador
Python (relevante em execuções longas ou via ponte QGIS, cujo interpretador
Python persiste entre chamadas).

## 6. Relatório de consistência

`relatorio_vegsec_selecionados.py` audita cada geopackage de saída,
produzindo um JSON (mesmo nome do `.gpkg`) com, por camada: número total de
polígonos, número de geometrias inválidas (varredura completa, em blocos,
via `IsValid()`), número de feições sem geometria, área total (ha) e área
agregada por UF e por bioma (via SQL agregado direto sobre o GeoPackage/
SQLite, usando o atributo `area_ha` já calculado no passo 4 acima — não há
necessidade de reprojeção geométrica para essa agregação).

## 7. Correção de geometrias inválidas

Caso o relatório acuse geometrias inválidas remanescentes na saída (o que
não é esperado no fluxo normal, já que a limpeza do passo 2 do cruzamento
deveria eliminá-las antes da interseção, mas pode ocorrer por reintrodução
de invalidade durante a própria operação de interseção geométrica),
`corrigir_geometrias_vegsec.py` lê a lista de camadas afetadas diretamente
dos JSONs de relatório e regrava, feição a feição, apenas a geometria das
que estão inválidas — usando o mesmo reparo `make_valid()`/`buffer(0)` — sem
alterar contagem de feições nem o atributo `area_ha` (o reparo de uma
invalidade típica é uma correção subpixel, sem efeito prático na área já
calculada). A correção também é resiliente/retomável, no mesmo padrão de
orçamento de tempo por chamada.

## 8. Limitações conhecidas

- O atributo `area_ha` de feições corrigidas pelo passo 7 não é
  recalculado após o reparo geométrico — decisão deliberada, dado que o
  reparo típico altera a geometria em escala subpixel.
- A correspondência UF → bioma é fixa no script; UFs com biomas adicionados
  ou removidos por atualização oficial do IBGE exigiriam atualização manual
  dessa tabela.
