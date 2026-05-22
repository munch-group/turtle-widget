# turtle-widget

An animated, [`turtle`](https://docs.python.org/3/library/turtle.html)-compatible drawing
widget for Jupyter notebooks, built on [anywidget](https://anywidget.dev).

You write ordinary turtle code in Python; the widget records each command and replays it as
a smooth canvas animation right below the cell. Because it is built on `anywidget`, it
behaves identically in **VS Code**, **JupyterLab**, **Notebook 7**, and **Colab**, and works
offline. Optionally it shows the cell source beside the canvas and highlights the active
line in sync with the animation (`show_code=True`).

## Installation

```bash
# pixi
pixi workspace channel add munch-group
pixi add turtle-widget

# conda
conda install -c munch-group turtle-widget

# pip
pip install turtle-widget
```

## Quick start

```python
from turtle_widget import Turtle

t = Turtle()
t.speed(6)
for _ in range(4):     # a square
    t.forward(120)
    t.left(90)
```

The widget appears automatically at the end of the cell — no need to put `t` on the last
line. You can also display it explicitly with `t.show()`, or evaluate `t`. Pass
`autoshow=False` to build a turtle without displaying it (useful for headless geometry
checks).

Most of the common `turtle` API is supported: `forward`/`backward`, `left`/`right`,
`goto`/`setheading`/`home`, `circle`, pen control (`penup`/`pendown`/`pensize`), colours
and fills (`pencolor`/`fillcolor`/`begin_fill`/`end_fill`), and markers/text
(`dot`/`stamp`/`write`). Note that the pen-width method is `pensize()` (the name `width` is
the canvas-size trait).

## Documentation

Full docs and a guided showcase live at
[munch-group.org/turtle-widget](https://munch-group.org/turtle-widget).

## Development

This repo is managed with [pixi](https://pixi.sh):

```bash
pixi run install-dev   # editable install into the pixi environment
pixi run test          # run the pytest suite
pixi run docs          # execute the documentation notebooks
pixi run api           # build the quartodoc API reference
```

## License

MIT
