"""Restore console-owned router rules at boot, before starting the proxy core."""
from . import db, router


def main():
    db.init_db()
    profile = router.configured()
    if profile:
        with router.system_transaction(profile, router.load_config(db.get_setting('campus_config'))):
            pass


if __name__ == '__main__':
    main()
