.PHONY: test test-local test-full-game lint lint-local build login new play status destroy

PLAYER_IMAGE ?= perfid-player

# --- Testing ---

test:
	docker build -f Dockerfile.test -t perfid-test .
	docker run --rm perfid-test

test-local:
	python3 -m pytest -v test_gpg.py test_logger.py test_game_state.py \
		test_message_router.py test_orders.py test_prompt.py \
		test_jdip_adapter.py test_game_loop.py test_decrypt_log.py
	python3 -m pytest -v test_perfid.py

test-full-game:
	bash test_full_game

# --- Linting ---

lint:
	docker build -f Dockerfile.test -t perfid-test .
	docker run --rm perfid-test python3 -m ruff check .

lint-local:
	python3 -m ruff check .

# --- Container lifecycle ---

build:
	docker build -f Dockerfile.player -t $(PLAYER_IMAGE) .

# Authenticate claude CLI and bake credentials into the image.
# Runs claude as player, copies resulting credentials to
# /root/.claude-credentials (where game_loop re-injects them
# each turn after decrypting per-power memory).
login:
	@docker image inspect $(PLAYER_IMAGE) >/dev/null 2>&1 || $(MAKE) build
	@ctr=$$(docker create -it --entrypoint "" $(PLAYER_IMAGE) \
		sh -c 'sudo -u player claude && \
			mkdir -p /root/.claude-credentials && \
			cp -a /home/player/.claude/. /root/.claude-credentials/') && \
	docker start -ai "$$ctr" && \
	docker commit \
		--change 'ENTRYPOINT ["python3", "/perfid/perfid"]' \
		--change 'CMD ["--help"]' \
		--change 'WORKDIR /games' \
		"$$ctr" $(PLAYER_IMAGE) >/dev/null && \
	docker rm "$$ctr" >/dev/null && \
	echo "Credentials baked into $(PLAYER_IMAGE)."

# Create a new game (runs inside container)
new:
	@test -n "$(GAME)" || { echo "Usage: make new GAME=<game-id>"; exit 1; }
	@docker image inspect $(PLAYER_IMAGE) >/dev/null 2>&1 || $(MAKE) build
	@mkdir -p perfid-games
	docker run --rm \
		-v "$$(cd perfid-games && pwd):/games" \
		-e PERFID_GAMES_DIR=/games \
		$(PLAYER_IMAGE) new $(GAME)

# Run the game loop (runs inside container)
play:
	@test -n "$(GAME)" || { echo "Usage: make play GAME=<game-id>"; exit 1; }
	docker run --rm -it \
		-v "$$(cd perfid-games && pwd):/games" \
		-v "perfid-sessions-$(GAME):/home/player/.claude" \
		-e PERFID_GAMES_DIR=/games \
		$(PLAYER_IMAGE) play $(GAME)

# Print game status (runs inside container)
status:
	@test -n "$(GAME)" || { echo "Usage: make status GAME=<game-id>"; exit 1; }
	docker run --rm \
		-v "$$(cd perfid-games && pwd):/games" \
		-e PERFID_GAMES_DIR=/games \
		$(PLAYER_IMAGE) status $(GAME)

# Tear down volumes
destroy:
	@test -n "$(GAME)" || { echo "Usage: make destroy GAME=<game-id>"; exit 1; }
	docker volume rm "perfid-sessions-$(GAME)" 2>/dev/null || true
	@if [ -d "perfid-games/$(GAME)" ]; then \
		printf "Delete game directory perfid-games/$(GAME)? [y/N] "; \
		read answer; \
		if [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]; then \
			rm -rf "perfid-games/$(GAME)"; \
			echo "Game directory deleted."; \
		else \
			echo "Game directory kept."; \
		fi; \
	fi
	@echo "Game '$(GAME)' destroyed."
