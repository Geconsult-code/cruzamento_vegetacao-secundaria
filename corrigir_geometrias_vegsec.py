# -*- coding: utf-8 -*-
r"""
corrigir_geometrias_vegsec.py

Regrava (in-place) as geometrias INVALIDAS identificadas pelo relatorio de
consistencia (relatorio_vegsec_selecionados.py) diretamente nos dois
geopackages de saida do cruzamento vegetacao secundaria x APP/RL/AUR:
    Vegetacao_Secundaria\VS_Imoveis_Selecionados_Nao_Analisados.gpkg
    Vegetacao_Secundaria\VS_Imoveis_Selecionados_Analisados.gpkg

Usa make_valid() com fallback buffer(0) (mesmo padrao ja validado no projeto
de conformidade, incl. no caso patologico do Amazonas). NAO mexe em
geometrias validas nem em contagem de feicoes (so SetFeature na geometria de
quem esta invalido, nunca cria ou remove linha) - o atributo area_ha da(s)
poucas feicao(oes) corrigida(s) NAO e recalculado (o reparo tipico de uma
invalidade e uma correcao subpixel, sem efeito pratico na area).

Requer as bindings Python do GDAL (pacote "osgeo") - ver requirements.txt /
README. Le a lista de camadas a corrigir diretamente dos JSONs de relatorio
(rode relatorio_vegsec_selecionados.py antes). Resiliente/retomavel via
_progresso_correcao_vegsec.json (guarda o ultimo FID processado por
camada). Cada chamada de rodada() processa um orcamento de tempo
(BUDGET_S) e para - rodar de novo continua exatamente de onde parou.
"""

import os
import json
import time
import gc

from osgeo import ogr, gdal
import shapely

gdal.UseExceptions()

BASE_ROOT = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR"
VEG_DIR = os.path.join(BASE_ROOT, "Vegetacao_Secundaria")

ARQUIVOS = {
    "Nao_Analisados": os.path.join(VEG_DIR, "VS_Imoveis_Selecionados_Nao_Analisados.gpkg"),
    "Analisados": os.path.join(VEG_DIR, "VS_Imoveis_Selecionados_Analisados.gpkg"),
}

PROGRESSO = os.path.join(VEG_DIR, "_progresso_correcao_vegsec.json")

CHUNK = 100000    # tamanho do bloco de FIDs por leitura/transacao
BUDGET_S = 38     # orcamento de tempo por chamada de rodada()


def carregar_worklist():
    """Le os JSONs de relatorio (mesmo nome do gpkg) e monta a lista de
    camadas com num_geometrias_invalidas > 0."""
    itens = []
    for categoria, path in ARQUIVOS.items():
        json_path = os.path.splitext(path)[0] + ".json"
        if not os.path.isfile(json_path):
            raise FileNotFoundError(
                f"Relatorio nao encontrado: {json_path} - rode "
                f"relatorio_vegsec_selecionados.py antes.")
        with open(json_path, "r", encoding="utf-8") as f:
            rel = json.load(f)
        for layer, info in rel["layers"].items():
            n_inval = info.get("num_geometrias_invalidas") or 0
            if n_inval > 0:
                itens.append({
                    "categoria": categoria, "path": path, "layer": layer,
                    "num_invalidas_relatorio": n_inval,
                    "num_poligonos": info["num_poligonos"],
                })
    itens.sort(key=lambda x: (x["categoria"], x["layer"]))
    return itens


def carregar_progresso():
    if os.path.isfile(PROGRESSO):
        with open(PROGRESSO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_progresso(prog):
    with open(PROGRESSO, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=1)


def reparar_geom(ogr_geom):
    wkb = bytes(ogr_geom.ExportToIsoWkb())
    g = shapely.from_wkb(wkb)
    try:
        novo = shapely.make_valid(g)
    except Exception:
        try:
            novo = g.buffer(0)
        except Exception:
            return None
    if novo is None or novo.is_empty:
        return None
    return ogr.CreateGeometryFromWkb(shapely.to_wkb(novo))


def processar_chunk(item, fid_lo, fid_hi):
    ds = ogr.Open(item["path"], update=1)
    lyr = ds.GetLayerByName(item["layer"])
    fidcol = lyr.GetFIDColumn() or "fid"
    lyr.SetAttributeFilter(f'"{fidcol}" >= {fid_lo} AND "{fidcol}" < {fid_hi}')
    lyr.ResetReading()

    n_scan = n_inval = n_fix = n_desc = 0
    lyr.StartTransaction()
    feat = lyr.GetNextFeature()
    while feat is not None:
        n_scan += 1
        geom = feat.GetGeometryRef()
        if geom is not None and not geom.IsValid():
            n_inval += 1
            novo = reparar_geom(geom)
            if novo is not None:
                if novo.GetGeometryType() == ogr.wkbPolygon:
                    novo = ogr.ForceToMultiPolygon(novo)
                feat.SetGeometry(novo)
                lyr.SetFeature(feat)
                n_fix += 1
            else:
                n_desc += 1
        feat = lyr.GetNextFeature()
    lyr.CommitTransaction()
    lyr.SetAttributeFilter(None)
    ds = None
    return n_scan, n_inval, n_fix, n_desc


def rodada():
    t0 = time.time()
    worklist = carregar_worklist()
    prog = carregar_progresso()

    if not worklist:
        print("Nenhuma camada com geometrias invalidas segundo o relatorio. Nada a fazer.")
        return True

    log = []
    for item in worklist:
        chave = f"{item['categoria']}::{item['layer']}"
        st = prog.get(chave, {
            "fid_proximo": 0, "concluido": False,
            "total_invalidas": 0, "total_corrigidas": 0, "total_descartadas": 0,
            "total_escaneadas": 0,
        })
        if st["concluido"]:
            continue

        total = item["num_poligonos"]
        while st["fid_proximo"] <= total:
            if time.time() - t0 > BUDGET_S:
                prog[chave] = st
                salvar_progresso(prog)
                log.append(f"[PAUSA orcamento] {chave} em fid={st['fid_proximo']}/{total}")
                print("\n".join(log))
                print(f"\nRODADA parcial: {round(time.time()-t0,1)}s. Rode de novo pra continuar.")
                return False

            lo = st["fid_proximo"]
            hi = lo + CHUNK
            n_scan, n_inval, n_fix, n_desc = processar_chunk(item, lo, hi)
            st["fid_proximo"] = hi
            st["total_escaneadas"] += n_scan
            st["total_invalidas"] += n_inval
            st["total_corrigidas"] += n_fix
            st["total_descartadas"] += n_desc

            if total == 0:
                break

        st["concluido"] = True
        prog[chave] = st
        salvar_progresso(prog)
        gc.collect()
        log.append(
            f"[OK] {chave}: escaneadas={st['total_escaneadas']} invalidas={st['total_invalidas']} "
            f"corrigidas={st['total_corrigidas']} descartadas={st['total_descartadas']}"
        )

    salvar_progresso(prog)
    print("\n".join(log))
    print(f"\nRODADA completa: TODAS as {len(worklist)} camadas processadas. Tempo: {round(time.time()-t0,1)}s")
    return True


def verificar():
    """Reescaneia todas as camadas do worklist e confere que zero geometrias seguem invalidas."""
    worklist = carregar_worklist()
    problemas = []
    for item in worklist:
        ds = ogr.Open(item["path"])
        lyr = ds.GetLayerByName(item["layer"])
        n_inval = 0
        lyr.ResetReading()
        feat = lyr.GetNextFeature()
        while feat is not None:
            geom = feat.GetGeometryRef()
            if geom is not None and not geom.IsValid():
                n_inval += 1
            feat = lyr.GetNextFeature()
        ds = None
        if n_inval > 0:
            problemas.append({"categoria": item["categoria"], "layer": item["layer"], "restantes": n_inval})
    print(json.dumps(problemas, ensure_ascii=False, indent=1))
    print(f"\nCamadas ainda com invalidas: {len(problemas)} de {len(worklist)}")
    return problemas


if __name__ == "__main__":
    while not rodada():
        pass
    print("\nLOTE COMPLETO.")
