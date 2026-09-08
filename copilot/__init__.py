"""
copilot
=======
Asistente de planificacion VFR en lenguaje natural.

Principio rector de todo el paquete:

    El LLM no sabe nada. No decide, no calcula y no aporta conocimiento
    propio. Interpreta la pregunta, elige que herramienta determinista
    llamar, y redacta con lo que esa herramienta devolvio. Si el dato no
    esta, lo dice.

El modelo de lenguaje es el unico componente NO interpretable del sistema.
Por eso queda confinado por arquitectura a un rol donde sus modos de falla
no pueden alcanzar al veredicto GO / CAUTION / NO GO: la decision la sigue
tomando el motor deterministico, y el asistente solo la transcribe.
"""
