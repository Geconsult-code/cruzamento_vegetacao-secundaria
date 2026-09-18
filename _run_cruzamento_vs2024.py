
import sys, os, time
sys.path.insert(0, r"C:\Users\User\OneDrive\Documentos\GitHub\cruzamento_vegetacao-secundaria")
import importlib.util
spec = importlib.util.spec_from_file_location("cruz2024", r"C:\Users\User\OneDrive\Documentos\GitHub\cruzamento_vegetacao-secundaria\2_cruzamento_espacial_VS_2024.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

log_f = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cadastro Ambiental Rural_Maio2026\_log_cruzamento_vs2024.txt"
done_f = r"C:\Users\User\Dropbox\#CONSULTANCY\PLANAVEG\GEODATABASE\GEOPACKAGE\Cadastro Ambiental Rural_Maio2026\_done_cruzamento_vs2024.txt"

def log(msg):
    with open(log_f, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

t0 = time.time()
log(f"=== INICIO {time.ctime()} ===")
while True:
    try:
        ok = mod.rodada()
    except Exception as e:
        log(f"[ERRO] {e!r}")
        raise
    if ok:
        break

log(f"=== FIM {time.ctime()} tempo_total={round(time.time()-t0,1)}s ===")
with open(done_f, "w", encoding="utf-8") as f:
    f.write("done\n")
