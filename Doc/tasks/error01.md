зашел в админку. там ошибки.
1. в streamlit app в разделах Сценарии, входящие, клбючевые слова, Статистика вот такое:
Welcome / comment-to-DM / handover — это шаблоны автоответов. Можно изменить текст без перезапуска. {first_name}, {tg_link}, {disclaimer} — подставляются автоматически.

RuntimeError: Event loop is closed
Traceback:
File "/app/admin/streamlit_app.py", line 49, in <module>
    main()
File "/app/admin/streamlit_app.py", line 45, in main
    PAGES[page_label](actor=actor)  # type: ignore[index]
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/admin/pages/_03_scenarios.py", line 20, in render
    rows = asyncio.run(sc_data.list_all())
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/asyncio/runners.py", line 195, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/asyncio/runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/asyncio/base_events.py", line 691, in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
File "/app/admin/data/scenarios.py", line 11, in list_all
    return await pool.fetch(
           ^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/pool.py", line 628, in fetch
    async with self.acquire() as con:
               ^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/pool.py", line 1056, in __aenter__
    self.connection = await self.pool._acquire(self.timeout)
                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/pool.py", line 896, in _acquire
    return await _acquire_impl()
           ^^^^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/pool.py", line 881, in _acquire_impl
    proxy = await ch.acquire()  # type: PoolConnectionProxy
            ^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/pool.py", line 161, in acquire
    await self.connect()
File "/app/.venv/lib/python3.12/site-packages/asyncpg/pool.py", line 153, in connect
    self._con = await self._pool._get_new_connection()
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/pool.py", line 538, in _get_new_connection
    con = await self._connect(
          ^^^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/connection.py", line 2443, in connect
    return await connect_utils._connect(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/connect_utils.py", line 1218, in _connect
    conn = await _connect_addr(
           ^^^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/connect_utils.py", line 1054, in _connect_addr
    return await __connect_addr(params, True, *args)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/connect_utils.py", line 1099, in __connect_addr
    tr, pr = await connector
             ^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/asyncpg/connect_utils.py", line 969, in _create_ssl_connection
    tr, pr = await loop.create_connection(
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/asyncio/base_events.py", line 1083, in create_connection
    infos = await self._ensure_resolved(
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/asyncio/base_events.py", line 1466, in _ensure_resolved
    return await loop.getaddrinfo(host, port, family=family, type=type,
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/asyncio/base_events.py", line 905, in getaddrinfo
    return await self.run_in_executor(
                 ^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/asyncio/base_events.py", line 854, in run_in_executor
    self._check_closed()
File "/usr/local/lib/python3.12/asyncio/base_events.py", line 545, in _check_closed
    raise RuntimeError('Event loop is closed')

2.  при вводе пароля ошибся с раскладкой. вот так реагирует на пароль кирилицей:

TypeError: comparing strings with non-ASCII characters is not supported
Traceback:
File "/app/admin/streamlit_app.py", line 49, in <module>
    main()
File "/app/admin/streamlit_app.py", line 33, in main
    actor = require_auth()
            ^^^^^^^^^^^^^^
File "/app/admin/auth.py", line 46, in require_auth
    and secrets.compare_digest(password, expected_pass)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
