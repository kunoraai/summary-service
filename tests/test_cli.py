from summary_service.cli import build_parser


def test_cli_exposes_migrate_api_worker_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["migrate"]).command == "migrate"
    assert parser.parse_args(["api"]).command == "api"
    assert parser.parse_args(["worker"]).command == "worker"
