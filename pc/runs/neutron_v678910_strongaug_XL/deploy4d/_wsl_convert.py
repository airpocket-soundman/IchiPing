import neutron_converter_SDK_26_03.neutron_converter as nc
from pathlib import Path
in_p  = Path("/mnt/d/GitHub/IchiPing/pc/runs/neutron_v678910_strongaug_XL/deploy4d/pinto_final/model_fp32_4d_full_integer_quant.tflite")
out_p = Path("/mnt/d/GitHub/IchiPing/pc/runs/neutron_v678910_strongaug_XL/deploy4d/pinto_neutron_sdk26_03.tflite")
b = nc.convertModel(list(in_p.read_bytes()), "mcxn94x")
out_p.write_bytes(bytes(b))
print("out", out_p.stat().st_size, "B")
