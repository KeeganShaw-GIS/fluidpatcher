#!/bin/bash

installdir=""
UPDATED=false
UPGRADE=false
PYTHON_PKG=""
ASK_TO_REBOOT=false

yesno() {
    read -r -p "$1 ([y]/n) " response < /dev/tty
    if [[ $response =~ ^(no|n|N)$ ]]; then
        false
    else
        true
    fi
}

noyes() {
    read -r -p "$1 (y/[n]) " response < /dev/tty
    if [[ $response =~ ^(yes|y|Y)$ ]]; then
        false
    else
        true
    fi
}

query() {
    read -r -p "$1 [$2] " response < /dev/tty
    if [[ $response == "" ]]; then
        response=$2
    fi
}

success() {
    echo -e "$(tput setaf 2)$1$(tput sgr0)"
}

inform() {
    echo -e "$(tput setaf 6)$1$(tput sgr0)"
}

warning() {
    echo -e "$(tput setaf 3)$1$(tput sgr0)"
}

failout() {
    echo -e "$(tput setaf 1)$1$(tput sgr0)"
    exit 1
}

sysupdate() {
    if ! $UPDATED || $UPGRADE; then
        echo "Updating package indexes..."
        if { sudo apt-get update 2>&1 || echo E: update failed; } | grep '^[WE]:'; then
            warning "Updating incomplete"
        fi
        sleep 3
        UPDATED=true
        if $UPGRADE; then
            echo "Upgrading your system..."
            if { sudo DEBIAN_FRONTEND=noninteractive apt-get -y upgrade --with-new-pkgs 2>&1 \
                || echo E: upgrade failed; } | grep '^[WE]:'; then
                warning "Encountered problems during upgrade"
            fi
            sudo apt-get clean && sudo apt-get autoclean
            sudo apt-get -qqy autoremove
            UPGRADE=false
        fi
    fi
}

apt_pkg_install() {
    sysupdate
    echo "Installing package $1..."
    if { sudo DEBIAN_FRONTEND=noninteractive apt-get -y --no-install-recommends install "$1" 2>&1 \
        || echo E: install failed; } | grep '^[WE]:'; then
        if [[ ! $2 == "optional" ]]; then
            failout "Problems installing $1!"
        else
            warning "Problems installing $1!"
        fi
    fi
}

pip_install() {
    echo "Installing Python module $1..."
    if ! { sudo -H pip3 install "$1" &> /dev/null; } then
        if [[ ! $2 == "optional" ]]; then
            failout "Failed to install $1!"
        else
            warning "Failed to install $1!"
        fi
    fi
}


sleep 1 # give curl time to print info

## get options from user

RED='\033[0;31m'
YEL='\033[1;33m'
NC='\033[0m'
echo -e "
 ${YEL}           o
     o───┐  │  o
      ${RED}___${YEL}│${RED}__${YEL}│${RED}__${YEL}│${RED}___
     /             \  ${YEL}o   ${NC}SquishBox/Headless Pi Synth Installer
 ${YEL}o───${RED}┤  ${NC}_________  ${RED}│  ${YEL}│     ${NC}by GEEK FUNK LABS
     ${RED}│ ${NC}│ █ │ █ █ │ ${RED}├${YEL}──┘     ${NC}geekfunklabs.com
     ${RED}│ ${NC}│ █ │ █ █ │ ${RED}│
     \_${NC}│_│_│_│_│_│${RED}_/${NC}
"
inform "This script installs/updates software and optional extras
for the SquishBox or headless Raspberry Pi synth."
warning "Always be careful when running scripts and commands copied
from the internet. Ensure they are from a trusted source."
echo "If you want to see what this script does before running it,
hit ctrl-C and enter 'curl -L https://tinyurl.com/kfluidpather | more'
View original code by GeekFunkLabs at
https://github.com/GeekFunkLabs/fluidpatcher
View the full source code at
https://github.com/KeeganShaw-GIS/fluidpatcher
Report issues with this script at
https://github.com/KeeganShaw-GIS/fluidpatcher/issues

Choose your install options. Empty responses will use the [default options].
Setup will begin after all questions are answered.
"

ENVCHECK=true
if test -f /etc/os-release; then
    if ! { grep -q ^Raspberry /proc/device-tree/model; } then
        ENVCHECK=false
    fi
    if ! { grep -q bullseye /etc/os-release; } then
        ENVCHECK=false
    fi
fi
if ! ($ENVCHECK); then
    warning "This software is designed for the latest Raspberry Pi OS (Bullseye),"
    warning "which does not appear to be the situation here. YMMV!"
    if noyes "Proceed anyway?"; then
        exit 1
    fi
fi

AUDIOCARDS=(`cat /proc/asound/cards | sed -n 's/.*\[//;s/ *\].*//p'`)

echo "Set up controls for Headless Pi Synth:"
query "    MIDI channel for controls" "1"; ctrls_channel=$response
query "    Previous patch momentary CC#" "21"; decpatch=$response
query "    Next patch momentary CC#" "22"; incpatch=$response
query "    Bank advance momentary CC#" "23"; bankinc=$response


query "Enter install location" $HOME; installdir=$response
if ! [[ -d $installdir ]]; then
    if noyes "'$installdir' does not exist. Create it and proceed?"; then
        exit 1
    else
        mkdir -p $installdir
    fi
fi

if yesno "Install/update synthesizer software?"; then
    install_synth=true
fi

if yesno "Update/upgrade your operating system?"; then
    UPGRADE=true
fi

defcard=0
echo "Which audio output would you like to use?"
echo "  0. No change"
echo "  1. Default"
i=2
for dev in ${AUDIOCARDS[@]}; do
    echo "  $i. $dev"

    if [[ $dev == "sndrpihifiberry" ]]; then
        defcard=$i
    fi
    ((i+=1))
done
query "Choose" $defcard; audiosetup=$response


echo ""
if ! yesno "Option selection complete. Proceed with installation?"; then
    exit 1
fi
warning "\nThis may take some time ... go make some coffee.\n"


## do things

# friendly file permissions for web file manager
umask 002 

if [[ $install_synth ]]; then
    # get dependencies
    inform "Installing/Updating supporting software..."
    sysupdate
    apt_pkg_install "python3-pip"
    apt_pkg_install "fluid-soundfont-gm"
    apt_pkg_install "ladspa-sdk" optional
    apt_pkg_install "swh-plugins" optional
    apt_pkg_install "tap-plugins" optional
    apt_pkg_install "wah-plugins" optional
    apt_pkg_install "i2c-tools" # For monitoring i2c port
    apt_pkg_install "python3-smbus" 
    apt_pkg_install "python3.11-venv"
    apt_pkg_install "python3-dev"
    apt_pkg_install "libopenjp2-7"

    # Get FOnt for display
    cp /usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf ~/DejaVuSerif-Bold.ttf
    # Use a virtual env instead for pip
    python3 -m pip install  sound_env
    source sound_env/bin/activate
    # TODO Write source sound_env/bin/activate to nano ~/.bashrc

    pip_install "oyaml"
    pip_install "RPi.GPIO"
    pip_install "RPLCD"
    pip_install "adafruit-blinka"
    pip_install "pillow"
    pip_install "image"
    pip_install "adafruit-circuitpython-ssd1306"

    
    # install/update fluidpatcher
    FP_VER=`sed -n '/^VERSION/s|[^0-9\.]*||gp' $installdir/patcher/__init__.py &> /dev/null`
    NEW_FP_VER=`curl -s https://api.github.com/repos/KeeganShaw-GIS/fluidpatcher/releases/latest | sed -n '/tag_name/s|[^0-9\.]*||gp'`
    if [[ ! $FP_VER == $NEW_FP_VER ]]; then
        inform "Installing/Updating FluidPatcher version $NEW_FP_VER ..."
        wget -qO - https://github.com/KeeganShaw-GIS/fluidpatcher/tarball/master | tar -xzm
        fptemp=`ls -dt KeeganShaw-GIS-fluidpatcher-* | head -n1`
        cd $fptemp
        find . -type d -exec mkdir -p ../{} \;
        # copy files, but don't overwrite banks, config (i.e. yaml files)
        find . -type f ! -name "*.yaml" ! -name "hw_overlay.py" -exec cp -f {} ../{} \;
        find . -type f -name "*.yaml" -exec cp -n {} ../{} \;
        cd ..
        rm -rf $fptemp
        ln -s /usr/share/sounds/sf2/FluidR3_GM.sf2 SquishBox/sf2/ > /dev/null
        gcc -shared assets/patchcord.c -o patchcord.so
        sudo mv -f patchcord.so /usr/lib/ladspa
    fi

    # compile/install fluidsynth
#    FS_VER=`fluidsynth --version 2> /dev/null | sed -n '/runtime version/s|[^0-9\.]*||gp'`
#    BUILD_FS_VER=`curl -s https://api.github.com/repos/FluidSynth/fluidsynth/releases/latest | sed -n '/tag_name/s|[^0-9\.]*||gp'`
    # prefer FluidSynth version 2.3.2 until https://github.com/FluidSynth/fluidsynth/issues/1272 is remedied
    FS_VER='0'
	BUILD_FS_VER='2.3.2'
    if [[ ! $FS_VER == $BUILD_FS_VER ]]; then
        inform "Compiling latest FluidSynth from source..."
        echo "Getting build dependencies..."
        if { grep -q ^#deb-src /etc/apt/sources.list; } then
            sudo sed -i "/^#deb-src/s|#||" /etc/apt/sources.list
            UPDATED=false
            sysupdate
        fi
        if { sudo DEBIAN_FRONTEND=noninteractive apt-get build-dep fluidsynth -y --no-install-recommends 2>&1 \
            || echo E: install failed; } | grep '^[WE]:'; then
            warning "Couldn't get all dependencies!"
        fi
#        wget -qO - https://github.com/FluidSynth/fluidsynth/tarball/master | tar -xzm
        wget -qO - https://github.com/FluidSynth/fluidsynth/archive/refs/tags/v2.3.2.tar.gz | tar -xzm
#        fstemp=`ls -dt FluidSynth-fluidsynth-* | head -n1`
        fstemp='fluidsynth-2.3.2'
        mkdir $fstemp/build
        cd $fstemp/build
        echo "Configuring..."
        cmake ..
        echo "Compiling..."
        make
        if { sudo make install; } then
            sudo ldconfig
        else
            warning "Unable to compile FluidSynth $BUILD_VER"
            apt_pkg_install "fluidsynth"
        fi
        cd ../..
        rm -rf $fstemp
    fi
fi

# set up audio
if (( $audiosetup > 0 )); then
    inform "Setting up audio..."
    sed -i "/audio.driver/d" $installdir/SquishBox/squishboxconf.yaml
    sed -i "/fluidsettings:/a\  audio.driver: alsa" $installdir/SquishBox/squishboxconf.yaml
    sed -i "/audio.alsa.device/d" $installdir/SquishBox/squishboxconf.yaml
    if (( $audiosetup > 1 )); then
        card=${AUDIOCARDS[$audiosetup-2]}
        sed -i "/audio.driver/a\  audio.alsa.device: hw:$card" $installdir/SquishBox/squishboxconf.yaml
    fi
fi

# set up services

inform "Enabling Headless Pi Synth startup service..."
chmod a+x $installdir/headlesspi.py
cat <<EOF | sudo tee /etc/systemd/system/squishbox.service
[Unit]
Description=Headless Pi Synth
After=local-fs.target

[Service]
Type=simple
ExecStart=$installdir/sound_env/bin/python $installdir/headlesspi.py
User=$USER
WorkingDirectory=$installdir
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable squishbox.service
sed -i "/^CHAN/s|[0-9]\+|$ctrls_channel|" $installdir/headlesspi.py
sed -i "/^DEC_PATCH/s|[0-9]\+|$decpatch|" $installdir/headlesspi.py
sed -i "/^INC_PATCH/s|[0-9]\+|$incpatch|" $installdir/headlesspi.py
sed -i "/^BANK_INC/s|[0-9]\+|$bankinc|" $installdir/headlesspi.py
ASK_TO_REBOOT=true



success "Tasks complete!"

if $ASK_TO_REBOOT; then
    warning "\nSome changes made to your system require a restart to take effect."
    echo "  1. Shut down"
    echo "  2. Reboot"
    echo "  3. Exit"
    query "Choose" "1"
    if [[ $response == 1 ]]; then
        sync && sudo poweroff
    elif [[ $response == 2 ]]; then
        sync && sudo reboot
    fi
fi
