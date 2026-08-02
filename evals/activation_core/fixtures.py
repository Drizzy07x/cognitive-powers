#!/usr/bin/env python3
"""Generate the throwaway workspaces the prompts are sent against.

Generated in process rather than checked in, for the reason
``controller_ab_fixtures`` already records: a tree read from the repository can
drift from the tree the measurement assumed, and a fixture that is also a
committed artifact eventually acquires an owner who edits it for another reason.

They are small on purpose. The prompt has to make sense in the directory, and
nothing more -- this harness measures whether a workflow is selected, not
whether it succeeds. A richer fixture would buy longer, costlier runs and no
extra signal about the selection.
"""

from __future__ import annotations

from pathlib import Path

WEBSHOP = {
    "README.md": "# widget-shop\n\nA small storefront. No build step yet.\n",
    "package.json": '{\n  "name": "widget-shop",\n  "version": "0.1.0"\n}\n',
    "src/index.html": (
        "<!doctype html>\n"
        "<html>\n"
        "  <body>\n"
        '    <button id="login">Log in</button>\n'
        '    <div id="cart"></div>\n'
        "  </body>\n"
        "</html>\n"
    ),
    "src/login.js": (
        "export function wireLogin(button) {\n"
        "  button.addEventListener('click', () => {\n"
        "    submit();\n"
        "  });\n"
        "}\n"
    ),
    "src/cart.js": (
        "export function total(items) {\n"
        "  let sum = 0;\n"
        "  for (const item of items) {\n"
        "    sum += item.price * item.qty;\n"
        "  }\n"
        "  return sum;\n"
        "}\n"
    ),
}

PYLIB = {
    "README.md": "# inventory\n\nStock tracking helpers. Partially tested.\n",
    "src/inventory.py": (
        '"""Stock helpers."""\n'
        "\n"
        "\n"
        "def restock(items, orders, log=None, dry=False, force=False):\n"
        "    out = []\n"
        "    for i in items:\n"
        "        for o in orders:\n"
        "            if o['sku'] == i['sku']:\n"
        "                if i['qty'] < o['min']:\n"
        "                    if not dry:\n"
        "                        if force or i['qty'] > 0:\n"
        "                            i['qty'] = i['qty'] + o['amount']\n"
        "                            if log is not None:\n"
        "                                log.append(i['sku'])\n"
        "                    out.append(i)\n"
        "    return out\n"
        "\n"
        "\n"
        "def value(items):\n"
        "    return sum(i['qty'] * i['price'] for i in items)\n"
    ),
    "tests/test_inventory.py": (
        "import unittest\n"
        "\n"
        "from src.inventory import value\n"
        "\n"
        "\n"
        "class ValueTests(unittest.TestCase):\n"
        "    def test_sums_quantity_times_price(self):\n"
        "        self.assertEqual(value([{'qty': 2, 'price': 3}]), 6)\n"
    ),
}

PAPER = {
    "README.md": "# notes\n\nReading notes on retrieval evaluation.\n",
    "NOTES.md": (
        "# Sampled softmax and the partition function\n"
        "\n"
        "Training maximises the log-likelihood log p(y|x) = s(x, y) - log Z(x),\n"
        "where the partition function Z(x) = sum over the full label set of\n"
        "exp(s(x, y')) is intractable when that set is large.\n"
        "\n"
        "The estimator replaces Z with a weighted sum over a proposal sample Q,\n"
        "correcting each drawn logit by subtracting log Q(y'). The correction is\n"
        "what makes the gradient unbiased in expectation; dropping it biases the\n"
        "model toward whatever the proposal distribution over-samples.\n"
        "\n"
        "Reported recall@k gains therefore depend on the proposal as much as on\n"
        "the scoring function, which is why ablations that change Q and k at the\n"
        "same time are uninterpretable.\n"
    ),
}

BARE = {"README.md": "# scratch\n\nNothing here yet.\n"}

TREES = {"webshop": WEBSHOP, "pylib": PYLIB, "paper": PAPER, "bare": BARE}


def materialize(name: str, destination: Path) -> Path:
    """Write one fixture tree under ``destination`` and return its root."""
    if name not in TREES:
        known = ", ".join(sorted(TREES))
        raise KeyError(f"unknown fixture {name!r}; known fixtures are {known}")
    destination.mkdir(parents=True, exist_ok=True)
    for relative, text in TREES[name].items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Newlines written verbatim so a fixture is byte-identical on every
        # platform; a workspace that differs by line ending is a workspace the
        # next run cannot be compared against.
        path.write_text(text, encoding="utf-8", newline="\n")
    return destination


def fixture_names() -> tuple[str, ...]:
    return tuple(sorted(TREES))
