import sys


def main() -> None:
    if "--autostart" in sys.argv[1:]:
        from listen_server.autostart import run_autostart

        run_autostart()
        return

    from listen_server.ui.main_window import run

    run()


if __name__ == "__main__":
    main()
