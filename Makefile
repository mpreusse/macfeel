# MacFeel development tasks. Run `make` for a list.

BUMP ?= patch

.PHONY: help test deb install uninstall run release clean

help:  ## Show this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

test:  ## Run the unit tests
	python3 -m unittest discover -s tests -t .

deb:  ## Build dist/macfeel_<version>_all.deb
	tools/build-deb.sh

install:  ## Build the .deb from this checkout and install it (asks for sudo)
	./install.sh

uninstall:  ## Remove the installed package and restore GNOME settings
	macfeel-uninstall

run:  ## Run daemon + tray + settings from the source tree (stop the service first)
	./macfeel.sh

release:  ## Tag and push the next version: make release [BUMP=patch|minor|major]
	tools/release.sh $(BUMP)

clean:  ## Remove build output and caches
	rm -rf dist
	find . -name __pycache__ -prune -exec rm -rf {} +
