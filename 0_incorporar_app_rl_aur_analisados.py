
# -*- coding: utf-8 -*-
"""
0_incorporar_app_rl_aur_analisados.py

Incorpora, nos arquivos NOVOS (Maio2026) de Analisados e Nao_Analisados,
as camadas de APP/RL/AUR "Selecionados" ja recortadas na rodada ANTERIOR
do pipeline (INCRA-CAR/Analise_Conformidade), que deixaram de vir nos
arquivos novos (esses agora só tem a camada do imovel inteiro, AREA_IMOVEL).

Copia layer a layer (pyogrio), idempotente (pula se a layer ja existe no
destino). Nao toca em Habilitados (ja vem completo no Maio2026).
"""
import os
import json
import pyogrio

UFS = ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT","PA","PB","PR","PE","PI",
       "RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
CATS = ["Analisados", "Nao_Analisados"]
TIPOS = ["APP", "RL", "AUR"]

NEW_DIR = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cadastro Ambiental Rural_Maio2026"
OLD_BASE = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR\Analise_Conformidade"
PROGRESSO = os.path.join(NEW_DIR, "_progresso_incorporar_app_rl_aur.json")


def carregar_progresso():
    if os.path.isfile(PROGRESSO):
        with open(PROGRESSO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_progresso(p):
    with open(PROGRESSO, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def rodada():
    prog = carregar_progresso()
    log = []
    for uf in UFS:
        for cat in CATS:
            new_f = os.path.join(NEW_DIR, f"{uf}_CAR_Imoveis_Selecionados_{cat}.gpkg")
            old_f = os.path.join(OLD_BASE, f"dados_saída_{uf}", f"{uf}_geopackage", f"{uf}_Conformidade_Imoveis_{cat}.gpkg")
            if not os.path.exists(new_f) or not os.path.exists(old_f):
                continue
            try:
                lyrs_new = set(pyogrio.list_layers(new_f)[:, 0])
            except Exception:
                lyrs_new = set()
            try:
                lyrs_old = set(pyogrio.list_layers(old_f)[:, 0])
            except Exception:
                lyrs_old = set()
            for tipo in TIPOS:
                chave = f"{uf}::{cat}::{tipo}"
                if prog.get(chave, {}).get("ok"):
                    continue
                layer_name = f"CAR_{uf}_{tipo}_Selecionados_{cat}"
                if layer_name in lyrs_new:
                    prog[chave] = {"ok": True, "motivo": "ja_existia"}
                    continue
                if layer_name not in lyrs_old:
                    prog[chave] = {"ok": True, "motivo": "nao_existe_na_origem"}
                    continue
                gdf = pyogrio.read_dataframe(old_f, layer=layer_name)
                pyogrio.write_dataframe(gdf, new_f, layer=layer_name, append=False)
                prog[chave] = {"ok": True, "motivo": "copiado", "n": len(gdf)}
                log.append(f"[OK] {chave}: {len(gdf)} feicoes copiadas")
    salvar_progresso(prog)
    print("\n".join(log) if log else "(nada novo a copiar)")
    print(f"\nTotal itens no progresso: {len(prog)}")


if __name__ == "__main__":
    rodada()
