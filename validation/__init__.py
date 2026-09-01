"""
validation
==========
Estudios metodologicos OFFLINE. Ninguno de estos modulos corre en tiempo de
ejecucion del motor: su proposito es producir y reproducir los resultados de
validacion que se reportan en el documento de tesis.

Misma categoria que risk/calibration.py y risk/sensitivity.py: apendices
metodologicos ejecutables.

Modulos
-------
terrain_strata.py : estratifica los aerodromos argentinos con archivo METAR
                    segun la rugosidad del terreno circundante (SRTM).
nwp_vs_metar.py   : compara el pronostico numerico contra la observacion real
                    en esos aerodromos, y mide el error en el veredicto.
"""
