import argparse
import time

from app.import_worker import run_next_job
from app.import_cleanup import maintain_import_storage


def main():
    parser = argparse.ArgumentParser(description="Process queued property import jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one job, then exit")
    args = parser.parse_args()
    print("Property import worker started. Press Ctrl+C to stop.", flush=True)
    while True:
        try:
            maintain_import_storage()
            result = run_next_job()
            if result is not None:
                print("Import completed." if result else "Import stopped; check its status/report.", flush=True)
        except KeyboardInterrupt:
            return
        except Exception as error:
            # Log the type only; database exceptions can contain private connection data.
            print(f"Worker temporarily unavailable ({type(error).__name__}); will retry.", flush=True)
            result = None
        if args.once:
            return
        if result is None:
            time.sleep(5)


if __name__ == "__main__":
    main()
