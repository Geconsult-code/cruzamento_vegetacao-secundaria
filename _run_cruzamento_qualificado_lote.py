
import sys, time, os
sys.path.insert(0, r"C:\Users\User\OneDrive\Documentos\GitHub\cruzamento_vegetacao-secundaria")
import importlib.util
spec = importlib.util.spec_from_file_location("cruz_vs_q", r"C:\Users\User\OneDrive\Documentos\GitHub\cruzamento_vegetacao-secundaria\2_cruzamento_espacial_VS_qualificado.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

LOG = os.path.join(mod.CAR_DIR, "_log_cruzamento_qualificado.txt")

def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\\n")

log(f"=== INICIO LOTE COMPLETO {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
t0 = time.time()
n_rodadas = 0
while True:
    completo = mod.rodada()
    n_rodadas += 1
    log(f"--- rodada {n_rodadas} concluida (completo={completo}), tempo acumulado={round(time.time()-t0,1)}s ---")
    if completo:
        break

log(f"=== LOTE COMPLETO: todos os itens processados. Tempo total: {round(time.time()-t0,1)}s ===")
with open(os.path.join(mod.CAR_DIR, "_done_cruzamento_qualificado.txt"), "w") as f:
    f.write("ok")
