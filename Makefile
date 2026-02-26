.PHONY: test lint deploy new bootstrap play status destroy

test:
	python3 -m pytest -v test_gpg.py test_logger.py test_game_state.py test_message_router.py test_orders.py test_prompt.py test_jdip_adapter.py
	bash test_perfid

lint:
	python3 -m ruff check .

deploy:
	@echo "perfid is not deployed — it runs locally via 'perfid play'"

new:
	@test -n "$(GAME)" || { echo "Usage: make new GAME=<game-id>"; exit 1; }
	./perfid new $(GAME)

bootstrap:
	@test -n "$(GAME)" || { echo "Usage: make bootstrap GAME=<game-id>"; exit 1; }
	./perfid bootstrap $(GAME)

play:
	@test -n "$(GAME)" || { echo "Usage: make play GAME=<game-id>"; exit 1; }
	./perfid play $(GAME)

status:
	@test -n "$(GAME)" || { echo "Usage: make status GAME=<game-id>"; exit 1; }
	./perfid status $(GAME)

destroy:
	@test -n "$(GAME)" || { echo "Usage: make destroy GAME=<game-id>"; exit 1; }
	./perfid destroy $(GAME)
