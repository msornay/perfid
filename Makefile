.PHONY: test deploy

test:
	python3 -m pytest -v test_gpg.py test_logger.py

deploy:
	@echo "perfid is not deployed — it runs locally via 'perfid play'"
