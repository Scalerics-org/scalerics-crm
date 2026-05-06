from main import create_parser

def test_parser_scrape_command():
    parser = create_parser()
    args = parser.parse_args(["scrape", "--query", "restaurante Montevideo", "--max", "50"])
    assert args.command == "scrape"
    assert args.query == "restaurante Montevideo"
    assert args.max == 50

def test_parser_run_all_command():
    parser = create_parser()
    args = parser.parse_args(["run-all", "--query", "peluqueria Salto"])
    assert args.command == "run-all"
    assert args.query == "peluqueria Salto"
    assert args.max == 100  # default

def test_parser_find_emails_command():
    parser = create_parser()
    args = parser.parse_args(["find-emails"])
    assert args.command == "find-emails"

def test_parser_send_emails_command():
    parser = create_parser()
    args = parser.parse_args(["send-emails"])
    assert args.command == "send-emails"
