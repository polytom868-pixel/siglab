# SigLab TUI Refactor Research (iter 16)

**Top 5 patterns**
1. **Reactive + watch, not imperative pushes.** Shared state is a `reactive()` field; side effects live in `watch_<name>()` that flips other reactives or calls `refresh()`. `BaseScreen` already does this for `is_loading`/`status_text`; extend it instead of poking children.
2. **`run_worker(exclusive=True, thread=True)` for I/O.** Set `is_loading=True`, schedule the worker, surface completion via a reactive or `post_message`.
3. **`on_button_pressed` + `BINDINGS` sharing one `action_*`.** Bind a key to `action_*`, give buttons matching `id`s, route both through that handler.
4. **Diff `DataTable` instead of `clear() + add_rows()` each refresh.** Compute missing/extra keys, add only new, remove gone, skip `update_cell` when unchanged. Biggest perf win for the tabular screens.
5. **Type reactives as `ClassVar[Reactive[T]]` for `--strict mypy`.** Without `ClassVar` mypy treats the descriptor as an instance attr.

**Top 3 anti-patterns**
- Building widgets in `render()` / rebuilding `compose()` per refresh — use static `compose()` + `watch_*` to flip content.
- Blocking the event loop in `on_mount` / `on_button_pressed`. Always `run_worker`.
- Duplicating `BINDINGS` and `is_loading` per screen — `BaseScreen` already owns them; stop copy-paste in subclasses.

**Snippets**
- `loading: ClassVar[Reactive[bool]] = reactive(False)` + `def watch_loading(self, _: bool) -> None: self.query_one(LoadingIndicator).set_class(self.loading, "-loading")`.
- `self.run_worker(self._load(), exclusive=True, thread=True)`; wrap body in `try/except` and `self.post_message(StatusUpdate(text))`.
- `BINDINGS = [("r", "refresh_now", "Refresh")]` + `def action_refresh_now(self) -> None: self._refresh_all()` + `def on_button_pressed(self, e: Button.Pressed) -> None: self.action_refresh_now()`.
- `for k in current - new: dt.remove_row(k); dt.add_rows(new_rows)` — never `dt.clear()` + full re-add on a 500-row screen.
