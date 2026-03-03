"""
Progress tracking for resumable scraping.
Maintains state of completed scrapes to support resume functionality.
"""
import json
import logging
from pathlib import Path
from typing import Set, Dict, Any
from datetime import datetime, timezone
import threading

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Tracks scraping progress for resume capability."""
    
    def __init__(self, progress_file: str):
        """
        Initialize progress tracker.
        
        Args:
            progress_file: Path to progress tracking file
        """
        self.progress_file = Path(progress_file)
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        
        self._lock = threading.Lock()
        self.completed_ids: Set[str] = set()
        self.failed_ids: Dict[str, int] = {}  # ID -> failure count
        self.metadata: Dict[str, Any] = {}
        
        self._load_progress()
        
        logger.info(f"Progress tracker initialized with {len(self.completed_ids)} completed IDs")
    
    def _load_progress(self) -> None:
        """Load progress from file if it exists."""
        if not self.progress_file.exists():
            logger.info("No existing progress file found, starting fresh")
            self.metadata = {
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "last_updated": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            }
            return
        
        try:
            with open(self.progress_file, 'r') as f:
                data = json.load(f)
                self.completed_ids = set(data.get("completed_ids", []))
                self.failed_ids = data.get("failed_ids", {})
                self.metadata = data.get("metadata", {})
                
            logger.info(f"Loaded progress: {len(self.completed_ids)} completed, "
                       f"{len(self.failed_ids)} failed")
            
        except Exception as e:
            logger.error(f"Failed to load progress file: {e}")
            logger.warning("Starting with empty progress")
            self.completed_ids = set()
            self.failed_ids = {}
            self.metadata = {
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "last_updated": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "error": f"Failed to load previous progress: {str(e)}"
            }
    
    def is_completed(self, external_id: str) -> bool:
        """
        Check if an ID has been completed.
        
        Args:
            external_id: External ID to check
            
        Returns:
            True if completed, False otherwise
        """
        return external_id in self.completed_ids
    
    def mark_completed(self, external_id: str) -> None:
        """
        Mark an ID as completed.
        
        Args:
            external_id: External ID to mark as completed
        """
        with self._lock:
            self.completed_ids.add(external_id)
            # Remove from failed if it was there
            self.failed_ids.pop(external_id, None)
            
        logger.debug(f"Marked as completed: {external_id}")
    
    def mark_failed(self, external_id: str, max_retries: int = 3) -> bool:
        """
        Mark an ID as failed and increment failure count.
        
        Args:
            external_id: External ID to mark as failed
            max_retries: Maximum number of retries allowed
            
        Returns:
            True if should retry, False if max retries exceeded
        """
        with self._lock:
            current_failures = self.failed_ids.get(external_id, 0)
            self.failed_ids[external_id] = current_failures + 1
            
            should_retry = self.failed_ids[external_id] < max_retries
            
            if should_retry:
                logger.warning(f"Failed attempt {self.failed_ids[external_id]} for {external_id}")
            else:
                logger.error(f"Max retries exceeded for {external_id}, giving up")
            
            return should_retry
    
    def should_process(self, external_id: str, max_retries: int = 3) -> bool:
        """
        Check if an ID should be processed.
        
        Args:
            external_id: External ID to check
            max_retries: Maximum number of retries allowed
            
        Returns:
            True if should process, False if already completed or max retries exceeded
        """
        if self.is_completed(external_id):
            return False
        
        failure_count = self.failed_ids.get(external_id, 0)
        return failure_count < max_retries
    
    def save_progress(self) -> None:
        """Save current progress to file."""
        with self._lock:
            try:
                self.metadata["last_updated"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                self.metadata["completed_count"] = len(self.completed_ids)
                self.metadata["failed_count"] = len(self.failed_ids)
                
                data = {
                    "completed_ids": list(self.completed_ids),
                    "failed_ids": self.failed_ids,
                    "metadata": self.metadata
                }
                
                # Write to temp file first, then rename for atomicity
                temp_file = self.progress_file.with_suffix('.tmp')
                with open(temp_file, 'w') as f:
                    json.dump(data, f, indent=2)
                
                temp_file.replace(self.progress_file)
                
                logger.debug(f"Progress saved: {len(self.completed_ids)} completed")
                
            except Exception as e:
                logger.error(f"Failed to save progress: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get progress statistics.
        
        Returns:
            Dictionary with progress stats
        """
        with self._lock:
            return {
                "completed_count": len(self.completed_ids),
                "failed_count": len(self.failed_ids),
                "retry_pending": sum(1 for count in self.failed_ids.values() if count < 3),
                "max_retries_exceeded": sum(1 for count in self.failed_ids.values() if count >= 3),
                "last_updated": self.metadata.get("last_updated"),
                "created_at": self.metadata.get("created_at")
            }
    
    def reset(self) -> None:
        """Reset all progress (use with caution)."""
        with self._lock:
            self.completed_ids.clear()
            self.failed_ids.clear()
            self.metadata = {
                "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "last_updated": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "reset": True
            }
            self.save_progress()
            
        logger.warning("Progress has been reset")
    
    def __del__(self):
        """Ensure progress is saved on destruction."""
        try:
            self.save_progress()
        except:
            pass
