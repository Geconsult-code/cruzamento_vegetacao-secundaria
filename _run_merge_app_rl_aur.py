
# -*- coding: utf-8 -*-
import os, subprocess, pyogrio, sys, time

OGR2OGR = r"C:\Program Files\QGIS 3.44.11\bin\ogr2ogr.exe"
UFS = ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT","PA","PB","PR","PE","PI",
       "RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
CATS = ["Analisados", "Nao_Analisados"]
TIPOS = ["APP", "RL", "AUR"]

NEW_DIR = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cadastro Ambiental Rural_Maio2026"
OLD_BASE = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\INCRA-CAR\Analise_Conformidade"
LOG = os.path.join(NEW_DIR, "_log_merge_app_rl_aur.txt")

def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\\n")
    print(msg)

def main():
    log(f"=== INICIO {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
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
                layer_name = f"CAR_{uf}_{tipo}_Selecionados_{cat}"
                if layer_name in lyrs_new:
                    continue
                if layer_name not in lyrs_old:
                    continue
                t0 = time.time()
                cmd = [OGR2OGR, "-f", "GPKG", "-update", "-append",
                       "-nln", layer_name, new_f, old_f, layer_name]
                r = subprocess.run(cmd, capture_output=True, text=True)
                dt = time.time() - t0
                if r.returncode == 0:
                    log(f"[OK] {uf}::{cat}::{tipo} em {dt:.1f}s")
                else:
                    log(f"[ERRO] {uf}::{cat}::{tipo}: {r.stderr[:2000]}")
    log(f"=== FIM {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    with open(os.path.join(NEW_DIR, "_done_merge.txt"), "w") as f:
        f.write("ok")

if __name__ == "__main__":
    main()
