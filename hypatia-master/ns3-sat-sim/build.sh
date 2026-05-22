#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_TAG="ns-3.47"
NS3_REPO="https://gitlab.com/nsnam/ns-3-dev.git"
NS3_DIR="${ROOT_DIR}/ns-3.47"
BASIC_SIM_REPO="https://github.com/snkas/basic-sim.git"
BASIC_SIM_DIR="${NS3_DIR}/contrib/basic-sim"
OLD_SIM_DIR="${ROOT_DIR}/simulator"

usage() {
  echo "Usage: bash build.sh [--help, --debug_all, --debug_minimal, --optimized, --optimized_with_tests]"
  echo "This build script fetches ns-3 3.47 and basic-sim main, preserves the custom satellite module, and builds the new ns-3 tree."
}

if [ "$1" == "--help" ]; then
  usage
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required to fetch ns-3 and basic-sim."
  exit 1
fi

if [ ! -d "${NS3_DIR}/wscript" ]; then
  echo "Cloning ns-3 ${NS3_TAG} into ${NS3_DIR}..."
  rm -rf "${NS3_DIR}"
  git clone --depth 1 --branch "${NS3_TAG}" "${NS3_REPO}" "${NS3_DIR}"
fi

if [ ! -d "${BASIC_SIM_DIR}/.git" ]; then
  echo "Cloning basic-sim master branch into ${BASIC_SIM_DIR}..."
  rm -rf "${BASIC_SIM_DIR}"
  git clone --depth 1 --branch master "${BASIC_SIM_REPO}" "${BASIC_SIM_DIR}"
else
  echo "Updating basic-sim to latest master..."
  git -C "${BASIC_SIM_DIR}" fetch --depth 1 origin master
  git -C "${BASIC_SIM_DIR}" checkout master
  git -C "${BASIC_SIM_DIR}" pull --ff-only origin master
fi

copy_custom_dir() {
  local src="$1"
  local dst="$2"
  if [ -d "${src}" ]; then
    rm -rf "${dst}"
    cp -a "${src}" "${dst}"
  fi
}

copy_custom_dir "${OLD_SIM_DIR}/src/satellite" "${NS3_DIR}/src/satellite"
copy_custom_dir "${OLD_SIM_DIR}/contrib/satellite-network" "${NS3_DIR}/contrib/satellite-network"
copy_custom_dir "${OLD_SIM_DIR}/scratch" "${NS3_DIR}/scratch"
copy_custom_dir "${OLD_SIM_DIR}/test_data" "${NS3_DIR}/test_data"

cd "${NS3_DIR}" || exit 1

# Configure the build
if [ "$1" == "--debug_all" ]; then
  ./waf configure --build-profile=debug --enable-mpi --enable-examples --enable-tests --enable-gcov --out=build/debug_all

elif [ "$1" == "--debug_minimal" ]; then
  ./waf configure --build-profile=debug --enable-mpi --out=build/debug_minimal

elif [ "$1" == "--optimized" ]; then
  ./waf configure --build-profile=optimized --enable-mpi --out=build/optimized

elif [ "$1" == "--optimized_with_tests" ]; then
  ./waf configure --build-profile=optimized --enable-mpi --enable-tests --out=build/optimized_with_tests

elif [ "$1" == "" ]; then
  ./waf configure --build-profile=debug --enable-mpi --enable-examples --enable-tests --enable-gcov --out=build/debug_all

else
  echo "Invalid build option: $1"
  usage
  exit 1
fi

# Perform the build
./waf -j4
