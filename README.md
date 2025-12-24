# DGX GPU Resource Manager

A lightweight, automated system to enforce SLURM resource allocation on shared GPU clusters (DGX). This tool monitors GPU processes, identifies unauthorized "non-SLURM" usage, and automatically terminates them to ensure fair resource distribution.

## 🚀 Why I Built This
As a researcher using a shared DGX cluster, I often found GPUs occupied by **rogue** processes or users bypassing the SLURM scheduler. This script was created to automate the policing of these resources, ensuring that scheduled jobs always get priority.

## 🛠 Features
- **Process Detection:** Distinguishes between SLURM jobs, Docker containers, and bare-metal processes.
- **Hierarchy Check:** Verifies if a process PID actually belongs to the active SLURM job ID.
- **Automated Cleanup:** Automatically kills processes that are consuming GPU memory but are not scheduled via SLURM.
- **Logging:** Maintains a detailed audit log (`gpu_kills.log`) of every terminated process.

## 📋 Prerequisites
- Python 3.x
- `psutil` library
- `sudo` access (required for killing processes of other users)
- Docker (if monitoring containerized workloads)
- Nvidia Drivers / `nvidia-smi`

## ⚙️ Usage

1. **Install Dependencies:**
   ```bash
   pip install psutil
   ```

2. **Grant Permissions:**
   The user running this script (e.g., admin) needs password-less sudo permission for the `kill` command.
   ```bash
   # Add to visudo
   username ALL=NOPASSWD: /usr/bin/kill
   ```

3. **Run Monitoring:**
   Start the background monitoring loop:
   ```bash
   nohup ./monitor.sh &
   ```

## 📄 Logs
- `current_gpu_status.log`: Real-time snapshot of the system.
- `gpu_kills.log`: History of all terminated non-compliant processes.
