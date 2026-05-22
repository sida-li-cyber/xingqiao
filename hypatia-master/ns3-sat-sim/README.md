# Low earth orbit satellite network simulation using ns-3

Hypatia makes use of ns-3 to simulate the satellite networks at packet-level
granularity. This repository has been updated to use ns-3 version 3.47 while
preserving the custom satellite module and the `satellite-network` contrib
module.

> **Compatibility**: This module is unchanged from the original Hypatia. All ns-3 simulations and APIs remain fully compatible. The realtime visualization pipeline uses a separate demo simulation core (`demo_sim_core.py`) that does not require ns-3 to be built — see `satviz/` and the main README for details.

It builds upon two ns-3 modules:

* `satellite` : Satellite movement calculation using SGP-4. This module is used
  by Hypatia to calculate the channel delay for each packet which traverses
  either a GSL or ISL. It was written by Pedro Silva at INESC-TEC. It can
  be found at:
  
  https://gitlab.inesctec.pt/pmms/ns3-satellite
  
  A copy of it is included in this repository with minor modifications.
  It is located at: simulator/src/satellite

* `basic-sim` : Simulation framework to make e.g., running end-to-end 
  TCP flows more easier. It can be found at:
  
  https://github.com/snkas/basic-sim
  
  This build setup now pulls the latest `main` branch of basic-sim into `ns3-sat-sim/ns-3.47/contrib/basic-sim`.


## Getting started

1. Install dependencies (inherited from `basic-sim` ns-3 module):
   ```
   sudo apt-get update
   sudo apt-get -y install build-essential g++ python3-dev python3-pip git openmpi-bin openmpi-common openmpi-doc libopenmpi-dev lcov gnuplot pkg-config
   pip install numpy statsmodels
   pip install git+https://github.com/snkas/exputilpy.git@main
   git submodule update --init --recursive
   ```

2. Build optimized:
   ```
   bash build.sh --optimized
   ```

3. When you need a full ns-3 3.47 rebuild after updating source or tests:
   ```
   bash rebuild.sh
   ```
