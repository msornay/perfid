.PHONY: test lint deploy

test:
	python3 -m pytest -v test_gpg.py test_logger.py test_game_state.py test_message_router.py

lint:
	python3 -m ruff check .

deploy:
	@echo "perfid is not deployed — it runs locally via 'perfid play'"
