"""Owner-invoked connectivity check using a fixed public synthetic sentence, never database data."""

import argparse

from app.gemini import Context, ProviderError, generate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-synthetic-free-tier",
        action="store_true",
        required=True,
        help="Owner has verified the configured model's free tier and authorizes this request",
    )
    parser.parse_args()
    try:
        answer = generate(
            lambda: [
                Context("synthetic-smoke-1", "Вымышленный отдел ведёт учебный реестр документов.")
            ]
        )
    except ProviderError as error:
        print("Gemini smoke check: " + error.code)
        raise SystemExit(1) from None
    print(
        f"Gemini: response schema and citations validated; findings={len(answer.findings)}. No business data sent."
    )


if __name__ == "__main__":
    main()
