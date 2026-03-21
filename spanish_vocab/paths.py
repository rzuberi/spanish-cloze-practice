import os


APP_DIRNAME = "spanish-cloze-practice"


def package_dir():
    return os.path.dirname(os.path.abspath(__file__))


def user_data_dir():
    override = os.environ.get("SPANISH_CLOZE_HOME")
    if override:
        root = os.path.abspath(os.path.expanduser(override))
    else:
        xdg_root = os.environ.get("XDG_DATA_HOME")
        if xdg_root:
            root = os.path.join(os.path.abspath(os.path.expanduser(xdg_root)), APP_DIRNAME)
        else:
            root = os.path.join(os.path.expanduser("~/.local/share"), APP_DIRNAME)
    if not os.path.isdir(root):
        os.makedirs(root)
    return root
