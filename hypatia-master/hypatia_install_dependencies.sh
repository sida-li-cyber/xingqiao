#!/usr/bin/env bash
# Main information
echo "Hypatia: installing dependencies"
echo ""
echo "It is highly recommended you use a recent Linux operating system (e.g., Ubuntu 22.04 or newer)."
echo "Python version 3.14 is recommended for this branch."
echo ""

PYTHON=python3.14
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    PYTHON=python3
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Installing git for repository-based dependency resolution..."
    sudo apt-get install -y git || exit 1
fi

$PYTHON -m pip install --upgrade pip setuptools wheel || exit 1

# General
sudo apt-get update || exit 1

# satgenpy
echo "Installing dependencies for satgenpy..."
$PYTHON -m pip install --upgrade numpy astropy ephem networkx sgp4 geopy matplotlib statsmodels cartopy || exit 1
sudo apt-get install -y libproj-dev proj-data proj-bin libgeos-dev || exit 1
$PYTHON -m pip install --upgrade git+https://github.com/snkas/exputilpy.git@master || exit 1

# ns3-sat-sim
echo "Installing dependencies for ns3-sat-sim..."
sudo apt-get -y install build-essential g++ python3-dev python3-pip git openmpi-bin openmpi-common openmpi-doc libopenmpi-dev lcov gnuplot pkg-config || exit 1
$PYTHON -m pip install --upgrade numpy statsmodels || exit 1
$PYTHON -m pip install --upgrade git+https://github.com/snkas/exputilpy.git@master || exit 1
git submodule update --init --recursive || exit 1

# satviz
echo "There are currently no additional Python dependencies for satviz beyond the Python environment."

# paper
echo "Installing dependencies for paper..."
$PYTHON -m pip install --upgrade numpy || exit 1
$PYTHON -m pip install --upgrade git+https://github.com/snkas/exputilpy.git@master || exit 1
$PYTHON -m pip install --upgrade git+https://github.com/snkas/networkload.git@master || exit 1
sudo apt-get install -y gnuplot || exit 1

# Confirmation dependencies are installed
echo ""
echo "Hypatia dependencies have been installed."
