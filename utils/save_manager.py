import os
import pandas as pd

def ensure_history_dir():
    """Crée le dossier history/ si nécessaire."""
    if not os.path.exists("history"):
        os.makedirs("history")

def save_dataframe(df, filename, save_name, meta=None):
    """
    Sauvegarde un DataFrame dans un CSV sous history/<filename>.
    - df : DataFrame à sauvegarder
    - filename : nom du fichier CSV (ex : "analyses_simple.csv")
    - save_name : nom donné par l'utilisateur pour cette sauvegarde
    - meta : dictionnaire de métadonnées à ajouter à chaque ligne (optionnel)
    """

    ensure_history_dir()

    path = os.path.join("history", filename)

    # copie pour la sauvegarde
    df2 = df.copy()

    # ajouter le nom de sauvegarde
    df2["save_name"] = save_name

    # ajouter les métadonnées éventuelles
    if meta:
        for k, v in meta.items():
            df2[k] = v

    # append si le fichier existe déjà
    if os.path.exists(path):
        df2.to_csv(path, mode="a", header=False, index=False)
    else:
        df2.to_csv(path, mode="w", header=True, index=False)

def load_history(filename):
    """
    Charge un fichier de sauvegarde (CSV) s'il existe.
    Retourne un DataFrame ou None.
    """
    path = os.path.join("history", filename)

    if os.path.exists(path):
        return pd.read_csv(path)
    return None
