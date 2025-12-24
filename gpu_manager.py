#!/usr/bin/env python3

# ==============================================================================
# GPU Resource Manager
# Author: Ezhini Rasendiran R
# Motivation: Automating the cleanup of 'zombie' processes on shared DGX nodes.
# ==============================================================================

import subprocess
import json
from collections import defaultdict
import sys
import traceback
import datetime
import time
import psutil

def safe_subprocess_run(cmd, shell=True):
    """Safely execute subprocess commands with error handling"""
    try:
        return subprocess.check_output(cmd, shell=shell, text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {cmd}")
        print(f"Error: {e.stderr}")
        return ""
    except Exception as e:
        print(f"Unexpected error running command {cmd}: {str(e)}")
        return ""

def check_pid_belongs_to_slurm_job(pid, jobid):
    """Check if a PID belongs to a specific SLURM job using process tree."""
    cmd = f"""ps -ef --forest | awk -v pid={pid} -v jobid={jobid} '
        $0 ~ "slurmstepd.*\\\\["jobid".batch\\\\]" {{print; p=NR; next}}
        (NR>=p) && (NR<=p+4) {{
            if ($2 == pid) found = 1;
        }}
        END {{
            print "\\nPID " pid " belongs to job " jobid "?: " (found ? "TRUE" : "FALSE");
            exit(!found)
        }}
    ' | grep -v "awk -v pid" """
    
    try:
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.returncode == 0 and "TRUE" in result.stdout
    except Exception as e:
        return False

def get_slurm_pid_hierarchy(job_id):
    """Get the process hierarchy for a Slurm job."""
    cmd = f"""ps -ef --forest | awk -v jobid={job_id} '
        $0 ~ "slurmstepd.*\\\\["jobid"\\\\.batch\\\\]" {{print; p=NR; next}}
        (NR>=p) && (NR<=p+4) {{print}}
    ' | grep -v "awk -v" """
    return safe_subprocess_run(cmd)

def get_process_user(pid):
    """Get the username of the user who owns the process."""
    try:
        cmd = f"ps -o user= -p {pid}"
        return subprocess.check_output(cmd.split()).decode().strip()
    except:
        return None
    
def get_container_info():
    """Get detailed container information using docker inspect."""
    containers = {}
    try:
        container_ids = safe_subprocess_run(['docker', 'container', 'ls', '--format', '{{.ID}}'], shell=False).strip().split('\n')
        for cid in container_ids:
            if not cid:
                continue
            try:
                inspect = safe_subprocess_run(['docker', 'inspect', cid], shell=False)
                info = json.loads(inspect)[0]
                containers[cid] = {
                    'name': info['Name'],
                    'mount_path': info['Mounts'][0]['Source'] if info['Mounts'] else '',
                    'user': info['Mounts'][0]['Source'].split('/')[2] if info['Mounts'] else 'unknown',
                    'binds': info['HostConfig']['Binds'] if 'Binds' in info['HostConfig'] else [],
                    'source': info['Mounts'][0]['Source'] if info['Mounts'] else ''
                }
            except Exception as e:
                print(f"Error inspecting container {cid}: {str(e)}")
    except Exception as e:
        print(f"Error getting container info: {str(e)}")
    return containers

def get_slurm_jobs():
    """Get information about running SLURM jobs."""
    slurm_jobs = {}
    try:
        jobs = safe_subprocess_run("squeue -h -o '%i %u %j %T %R'", shell=True).strip().split('\n')
        
        for job in jobs:
            try:
                job_id, user, job_name, state, node_list = job.split()
                scontrol_out = safe_subprocess_run(f"scontrol show job {job_id} -dd")
                
                # Extract GPU indices
                gpu_indices = []
                if 'IDX:' in scontrol_out:
                    idx_part = scontrol_out.split('IDX:')[1].split(')')[0]
                    for part in idx_part.split(','):
                        if '-' in part:
                            start, end = map(int, part.split('-'))
                            gpu_indices.extend(range(start, end + 1))
                        else:
                            try:
                                gpu_indices.append(int(part))
                            except ValueError:
                                continue
                
                # Extract WorkDir
                workdir = ""
                if 'WorkDir=' in scontrol_out:
                    workdir = scontrol_out.split('WorkDir=')[1].split()[0]
                
                # Extract RunTime
                runtime = ""
                if 'RunTime=' in scontrol_out:
                    runtime = scontrol_out.split('RunTime=')[1].split()[0]
                    if '-' in runtime:
                        days, time = runtime.split('-')
                        hours, mins, secs = time.split(':')
                        runtime = f"{days} days, {hours} hours, {mins} minutes, {secs} seconds"
                    else:
                        hours, mins, secs = runtime.split(':')
                        runtime = f"{hours} hours, {mins} minutes, {secs} seconds"
                
                slurm_jobs[job_id] = {
                    'user': user,
                    'job_name': job_name,
                    'state': state,
                    'node_list': node_list,
                    'gpu_indices': gpu_indices,
                    'workdir': workdir,
                    'runtime': runtime
                }
            except Exception as e:
                print(f"Error processing job {job}: {str(e)}")
                continue
    except Exception as e:
        print(f"Error in get_slurm_jobs: {str(e)}")
    
    return slurm_jobs

def get_gpu_processes():
    """Get detailed information about processes using GPUs."""
    gpu_processes = defaultdict(list)
    try:
        smi_output = safe_subprocess_run(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_memory', '--format=csv,noheader,nounits'], shell=False)
        gpu_info = safe_subprocess_run(['nvidia-smi', '-L'], shell=False).strip().split('\n')
        
        for line in smi_output.strip().split('\n'):
            if line:
                gpu_uuid, pid, memory = line.split(', ')
                for idx, info in enumerate(gpu_info):
                    if gpu_uuid in info:
                        gpu_processes[idx].append((int(pid), memory))
                        break
    except Exception as e:
        print(f"Error getting GPU process information: {str(e)}")
    
    return gpu_processes

def kill_non_slurm_process(pid, process_info):
    """Safely kill non-SLURM processes with logging"""
    try:
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"""
Process killed at {current_time}:
PID {pid} (Live GPU Memory: {process_info['memory']} MiB):
  - Execution Type: {process_info['type']}
  - Resource Management: Non-SLURM
  - Live GPU Memory Usage: {process_info['memory']} MiB"""

        if process_info['type'] == "Container":
            log_message += f"""
  - Container Name: {process_info.get('container_name', 'Unknown')}
  - Container User: {process_info['user']}
  - Mount Source: {process_info.get('mount_source', 'Unknown')}
  - Container Binds: {process_info.get('container_binds', 'Unknown')}"""
        else:
            log_message += f"\n  - User: {process_info['user']}"

        log_message += f"""
  - Command: {process_info['command']}
Killed PID {pid} successfully (Non-SLURM process)
----------------------------------------\n"""

        # Try graceful termination
        subprocess.run(['sudo', 'kill', str(pid)], check=True)
        time.sleep(5)
        
        # Force kill if still running
        if psutil.pid_exists(pid):
            subprocess.run(['sudo', 'kill', '-9', str(pid)], check=True)
            log_message += "Force kill was required\n"
            
        with open('gpu_kills.log', 'a') as f:
            f.write(log_message)
            
        print(f"Successfully terminated PID {pid} - See gpu_kills.log for details")
        
    except Exception as e:
        error_msg = f"Error killing PID {pid}: {str(e)}\n"
        print(error_msg)
        with open('gpu_kills.log', 'a') as f:
            f.write(error_msg)


def analyze_gpu_usage():
    """Main function to analyze and display GPU usage."""
    try:
        print("\nCollecting system information...")
        slurm_info = get_slurm_jobs()
        containers = get_container_info()
        gpu_processes = get_gpu_processes()
        
        print("\nGPU Usage Analysis:")
        print("=" * 80)
        
        # Get total number of GPUs in system
        total_gpus = len(safe_subprocess_run(['nvidia-smi', '-L'], shell=False).strip().split('\n'))
        
        # Iterate through all possible GPU indices
        for gpu_id in range(total_gpus):
            print(f"\nGPU {gpu_id}:")
            print("-" * 40)
            
            # Get SLURM jobs for this specific GPU
            gpu_specific_jobs = {
                job_id: job_info 
                for job_id, job_info in slurm_info.items() 
                if gpu_id in job_info['gpu_indices']
            }
            
            # Display GPU process information if exists
            if gpu_id in gpu_processes:
                for pid, memory in gpu_processes[gpu_id]:
                    try:
                        # Check if process is in container
                        container_info = None
                        for cid, info in containers.items():
                            try:
                                if str(pid) in safe_subprocess_run(['docker', 'top', cid], shell=False):
                                    container_info = info
                                    break
                            except:
                                continue
                        
                        process_type = "Container" if container_info else "Bare Metal"
                        
                        # Check if process belongs to any SLURM job
                        slurm_job_id = None
                        for job_id, job_info in gpu_specific_jobs.items():
                            if check_pid_belongs_to_slurm_job(pid, job_id):
                                if container_info:
                                    # Container process - check user match
                                    if container_info['user'] == job_info['user']:
                                        slurm_job_id = job_id
                                        break
                                else:
                                    # Bare metal process - check process user match
                                    if get_process_user(pid) == job_info['user']:
                                        slurm_job_id = job_id
                                        break
                        
                        slurm_status = f"SLURM & belongs to Jobid {slurm_job_id}" if slurm_job_id else "Non-SLURM"
                        cmd = safe_subprocess_run(['ps', '-p', str(pid), '-o', 'cmd='], shell=False).strip()
                        if slurm_status == "Non-SLURM":
                            kill_non_slurm_process(pid, {
                                'memory': memory,
                                'type': process_type,
                                'user': get_process_user(pid) if not container_info else container_info['user'],
                                'command': cmd,
                                'container_name': container_info['name'] if container_info else None,
                                'mount_source': container_info['source'] if container_info else None,
                                'container_binds': ', '.join(container_info['binds']) if container_info and container_info['binds'] else None
                            })
                        
                        print(f"PID {pid} (Live GPU Memory: {memory} MiB):")
                        print(f"  - Execution Type: {process_type}")
                        print(f"  - Resource Management: {slurm_status}")
                        print(f"  - Live GPU Memory Usage: {memory} MiB")
                        
                        if container_info:
                            print(f"  - Container Name: {container_info['name']}")
                            print(f"  - Container User: {container_info['user']}")
                            print(f"  - Mount Source: {container_info['source']}")
                            if container_info['binds']:
                                print(f"  - Container Binds: {', '.join(container_info['binds'])}")
                        else:
                            print(f"  - User: {get_process_user(pid)}")
                        
                        cmd = safe_subprocess_run(['ps', '-p', str(pid), '-o', 'cmd='], shell=False).strip()
                        if cmd:
                            print(f"  - Command: {cmd}")
                        else:
                            print("  - Command: Unknown")
                        print()
                        
                    except Exception as e:
                        print(f"Error processing PID {pid}: {str(e)}")
                        continue
            
            # Display SLURM job information for this GPU
            for job_id, job_info in gpu_specific_jobs.items():
                print(f"\nSLURM Job ID: {job_id}")
                print(f"  - User: {job_info['user']}")
                print(f"  - Job Name: {job_info['job_name']}")
                print(f"  - State: {job_info['state']}")
                print(f"  - Node List: {job_info['node_list']}")
                print(f"  - Working Directory: {job_info['workdir']}")
                print(f"  - Running Time: {job_info['runtime']}")
                print(f"  - Slurm PID Hierarchy:")
                hierarchy = get_slurm_pid_hierarchy(job_id)
                for line in hierarchy.splitlines():
                    print(f"    {line}")
    
    except Exception as e:
        print(f"Fatal error in analyze_gpu_usage: {str(e)}")
        traceback.print_exc()

if __name__ == "__main__":
    try:
        analyze_gpu_usage()
    except Exception as e:
        print(f"Script execution failed: {str(e)}")
        traceback.print_exc()
        sys.exit(1)