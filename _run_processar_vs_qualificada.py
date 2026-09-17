
import sys, time
sys.path.insert(0, r"C:\Users\User\OneDrive\Documentos\GitHub\cruzamento_vegetacao-secundaria")
import importlib.util
spec = importlib.util.spec_from_file_location("proc_vs_q", r"C:\Users\User\OneDrive\Documentos\GitHub\cruzamento_vegetacao-secundaria\1_processar_dados_VS_qualificada.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.SOMENTE_ESTES = []
t0 = time.time()
mod.main()
print("TEMPO TOTAL:", time.time() - t0)
