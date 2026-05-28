в админке нажал
streamlit app - Входящие. там нашел входящее и нажал кнопку "Открыть". вот такая ошибка:

streamlit.errors.StreamlitAPIException: st.session_state.page_selector cannot be modified after the widget with key page_selector is instantiated.

Traceback:
File "/app/admin/streamlit_app.py", line 49, in <module>
    main()
File "/app/admin/streamlit_app.py", line 45, in main
    PAGES[page_label](actor=actor)  # type: ignore[index]
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/admin/pages/_01_inbox.py", line 59, in render
    st.session_state["page_selector"] = "💬 Диалог"
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/streamlit/runtime/metrics_util.py", line 409, in wrapped_func
    result = non_optional_func(*args, **kwargs)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/.venv/lib/python3.12/site-packages/streamlit/runtime/state/session_state_proxy.py", line 113, in __setitem__
    get_session_state()[key] = value
    ~~~~~~~~~~~~~~~~~~~^^^^^
File "/app/.venv/lib/python3.12/site-packages/streamlit/runtime/state/safe_session_state.py", line 99, in __setitem__
    self._state[key] = value
    ~~~~~~~~~~~^^^^^
File "/app/.venv/lib/python3.12/site-packages/streamlit/runtime/state/session_state.py", line 516, in __setitem__
    raise StreamlitAPIException(
