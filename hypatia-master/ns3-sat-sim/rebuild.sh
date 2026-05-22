
# Create the basic-sim module
cd ns-3.47 || exit 1

# Rebuild whichever build is configured right now
./waf -j4 || exit 1
