import socket
from popper.util import Settings, Stats
from popper.asp import ClingoSolver, ClingoGrounder
from popper.constrain import Constrain
from popper.tester import Tester
from popper.core import Clause
from aggstrategy import aggregate_outcomes, aggregate_popper

import numpy as np
from popper.structural_tester import StructuralTester

from popper.util import load_kbpath
# ================================
#    GLOBAL STATE
# ================================
settings = None
stats = None
solver = None
grounder = None
constrainer = None
tester = None

current_hypothesis = None
current_before = None
current_min_clause = None
current_clause_size = 1


# ================================
#   UI
# ================================
def cli_prompt():
    banner = """
   _____               ____                             
  / ___/______   __   / __ \____  ____  ____  ___  _____
  \__ \/ ___/ | / /  / /_/ / __ \/ __ \/ __ \/ _ \/ ___/
 ___/ / /   | |/ /  / ____/ /_/ / /_/ / /_/ /  __/ /    
/____/_/    |___/  /_/    \____/ .___/ .___/\___/_/     
                              /_/   /_/                 
"""
    print(banner)


def initialisation():
    global nb_client
    global path_dir
    print("Please introduce ...")
    nb_client = int(input("- number of Popper clients: "))
    path_dir = input("- path to BK/Examples (folder): ")


# ================================
#   POPPER INITIALISE
# ================================
def popper_initialisation():
    global settings, stats, solver, grounder, constrainer, tester
    global current_hypothesis, current_before, current_min_clause, current_clause_size

    print("Initialising Distributed FILP...")

    # Load bias file only
    # The user provides a path where: BK, EX, BIAS normally exist
    # Here we assume bias.pl is inside that folder
    #bias_file = f"{path_dir}/bias.pl"
    
    kbpath = f"{path_dir}"
    _, _, bias_file = load_kbpath(kbpath)
    settings = Settings(bias_file, None, None)
    stats = Stats(log_best_programs=settings.info)
    solver = ClingoSolver(settings)
    grounder = ClingoGrounder()
    constrainer = Constrain()
    tester = StructuralTester()

    current_hypothesis = None
    current_before = None
    current_min_clause = None
    current_clause_size = 1


def convert_to_blpy(rule):
    r = rule.replace(" ", "")
    r = r.replace(":-", ",")
    r = r.replace("),", ");")
    if not r.endswith("."):
        r += "."
    return r
# ================================
#   SEND RULES TO CLIENT
# ================================


def tell_hypothesis(client, hyp):
    nb_cl = len(hyp)
    str_nb_cl = str(nb_cl)
    msg = f"tell( prgmlen({str_nb_cl}) )"
    client.send(msg.encode("utf-8")[:1024])
    client.recv(1024)
    for i in range(0,nb_cl):
        print("in loop")
        str_i = str(i)
        clause = "{" + hyp[i] + "}"
        #clause = hyp[i].replace(",", ";")
        print(f"clause = {clause}")
        msg = f"tell( prgm({str_i},{clause}) )"
        client.send(msg.encode("utf-8")[:1024])
        client.recv(1024)



def get_epsilon_pairs(client):
    global nb_client
    lepairs = []
    str_nb_client = str(nb_client)
    print(f"nb_client = {str_nb_client}")
    for i in range(1,nb_client+1):
        str_i = str(i)
        msg = f"ask( epair({str_i}) )"
        client.send(msg.encode("utf-8")[:1024])
        response = client.recv(1024)
        response = response.decode("utf-8")        
        lepairs.append(response)
    msg = "reset"
    client.send(msg.encode("utf-8")[:1024])
    client.recv(1024)
    return lepairs



def parse_epair(resp):
    # resp format: "epair(1,all,none)"
    parts = resp.strip().replace("epair(", "").replace(")", "").split(",")
    return parts[1], parts[2]   # (E+, E-)

def parse_epair(s):
    if not s or "(" not in s or ")" not in s:
        return ("none", "none")   # default safe outcome
    s = s.strip()
    inner = s[s.find("(")+1 : s.rfind(")")]
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) < 3:
        return ("none", "none")
    return parts[1], parts[2]


def to_prolog_clause(rule):
    head, body = rule
    head_str = Clause.to_code(head)  # ex: f(A)
    body_strs = [Clause.to_code(b) for b in body]
    if body_strs:
        return f"{head_str} :- {', '.join(body_strs)}."
    else:
        return f"{head_str}."
    
def normalize_rule_for_store(rule_str):
    """
    Transforme une règle Popper 'f(A):-has_car(A);three_wheels(B)' 
    → 'f(A) :- has_car(A), three_wheels(B).'
    """

    # nettoyer espaces
    rule = rule_str.strip()

    # enlever point final s'il existe (on le remettra nous-même)
    if rule.endswith('.'):
        rule = rule[:-1]

    # *** Popper utilise parfois ';' au lieu de ',' ***
    rule = rule.replace(";", ",")

    # Ajouter espace autour de ':-'
    if ":-" in rule:
        head, body = rule.split(":-")
        rule = f"{head.strip()} :- {body.strip()}"
    else:
        # fait rare mais au cas où c’est un fact
        rule = rule.strip()

    # remettre un point final
    if not rule.endswith("."):
        rule += "."

    return rule

# ================================
#   MAIN LOOP
# ================================

def run_server():
    global current_hypothesis, current_before, current_min_clause, current_clause_size, solver

    cli_prompt()
    initialisation()        # demande nb_client, path_dir
    popper_initialisation() # instancie settings, solver, tester, stats

    # Connexion au STORE 
    store = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    store.connect(("127.0.0.1", 8000))
    print("Connected to STORE.")

    # 1) Initial outcome = ("none","none") comme Flower 
    Eplus, Eminus = "none", "none"

    try:
        while True:

            # =======================================================
            # 1) POPPER STEP : generate hypothesis
            # =======================================================
            rules_arr, current_min_clause, current_before, current_clause_size, solver, solved = aggregate_popper(
                (Eplus, Eminus),          # jamais None
                settings,
                solver,
                grounder,
                constrainer,
                tester,
                stats,
                current_min_clause,
                current_before,
                current_hypothesis,
                current_clause_size
            )

            # Extraire règles Popper : strings utilisables par store
            rules_str = [Clause.to_code(r) for r in current_hypothesis] \
                        if current_hypothesis else []

            # Si aucune règle générée : STOP
            if not rules_arr or len(rules_arr[0]) == 0:
                print("No more rules produced. Stopping.")
                break

            raw_rules = rules_arr[0].tolist()

            # 2) Convertir en syntaxe BLPy pour le STORE
            rules_str = [normalize_rule_for_store(r) for r in raw_rules]
            print("Generated hypothesis (Store format):", rules_str)

            # 3) Envoi au STORE
            tell_hypothesis(store, rules_str)

            # =======================================================
            # 3) RÉCUPÉRER EPAIRS
            # =======================================================
            lepairs = get_epsilon_pairs(store)
            parsed = [parse_epair(e) for e in lepairs]

            Eplus, Eminus = aggregate_outcomes(parsed)
            print(f"Aggregated outcome = ({Eplus}, {Eminus})")

            # =======================================================
            # 4) CONDITION D'ARRÊT FILP
            # =======================================================
            if (Eplus, Eminus) == ("all", "none"):
                print(" Global solution found (ALL/NONE). Stopping.")
                break

    except Exception as e:
        print("Error:", e)

    finally:
        store.close()
        print("Connection to store closed.")


# ================================
#   RUN
# ================================
if __name__ == "__main__":
    nb_client = 0
    run_server()
