"""
Output handling for scraped records.
Writes structured JSON output to files.
"""
import json
import logging
from pathlib import Path
from typing import Dict, Any
from datetime import datetime, timezone
import threading

logger = logging.getLogger(__name__)


class OutputWriter:
    """Handles writing scraped records to JSON files."""
    
    def __init__(self, output_dir: str):
        """
        Initialize output writer.
        
        Args:
            output_dir: Directory to write output files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Thread-safe file writing
        self._lock = threading.Lock()
        
        # Initialize output files
        self.timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.success_file = self.output_dir / f"scraped_records_{self.timestamp}.jsonl"
        self.failed_file = self.output_dir / f"failed_records_{self.timestamp}.jsonl"
        
        logger.info(f"Output writer initialized. Success file: {self.success_file}")
        logger.info(f"Failed records file: {self.failed_file}")
    
    def write_success(self, record: Dict[str, Any]) -> None:
        """
        Write a successfully scraped record.
        
        Args:
            record: Scraped record data
        """
        self._write_record(self.success_file, record, "SUCCESS")
    
    def write_failure(self, external_id: str, url: str, error: str, 
                     entity_type: str = "unknown") -> None:
        """
        Write a failed scrape attempt.
        
        Args:
            external_id: External ID of the entity
            url: URL that failed
            error: Error message
            entity_type: Type of entity
        """
        record = {
            "external_id": external_id,
            "entity_type": entity_type,
            "url": url,
            "scrape_status": "FAILED",
            "error": error,
            "scraped_at": datetime.utcnow().isoformat() + "Z"
        }
        
        self._write_record(self.failed_file, record, "FAILURE")
    
    def _write_record(self, file_path: Path, record: Dict[str, Any], 
                     record_type: str) -> None:
        """
        Thread-safe write of a single record.
        
        Args:
            file_path: Path to output file
            record: Record to write
            record_type: Type of record (for logging)
        """
        with self._lock:
            try:
                with open(file_path, 'a', encoding='utf-8') as f:
                    json.dump(record, f, ensure_ascii=False)
                    f.write('\n')
                
                logger.debug(f"{record_type} record written: {record.get('external_id', 'unknown')}")
                
            except Exception as e:
                logger.error(f"Failed to write {record_type} record: {str(e)}")
                # Try to write to error file as backup
                if file_path != self.failed_file:
                    error_record = {
                        "error": "Failed to write record",
                        "original_record": str(record),
                        "exception": str(e),
                        "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                    }
                    try:
                        with open(self.output_dir / "write_errors.jsonl", 'a') as ef:
                            json.dump(error_record, ef)
                            ef.write('\n')
                    except:
                        pass  # Last resort failed
    
    def get_stats(self) -> Dict[str, int]:
        """
        Get statistics about written records.
        
        Returns:
            Dictionary with success and failure counts
        """
        success_count = self._count_lines(self.success_file)
        failed_count = self._count_lines(self.failed_file)
        
        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "total_count": success_count + failed_count
        }
    
    def _count_lines(self, file_path: Path) -> int:
        """Count lines in a file."""
        if not file_path.exists():
            return 0
        
        try:
            with open(file_path, 'r') as f:
                return sum(1 for _ in f)
        except Exception as e:
            logger.error(f"Failed to count lines in {file_path}: {e}")
            return 0


class BatchOutputWriter(OutputWriter):
    """Output writer with batching support and per-file record-count rotation."""

    def __init__(self, output_dir: str, batch_size: int = 100,
                 records_per_file: int = 5000):
        """
        Initialize batch output writer.

        Args:
            output_dir: Directory to write output files
            batch_size: Number of records to buffer in memory before flushing
            records_per_file: Max records per output file before rolling to next part
        """
        super().__init__(output_dir)
        self.batch_size = batch_size
        self.records_per_file = records_per_file
        self._success_batch = []
        self._failed_batch = []

        # File rotation state
        self._success_part = 1
        self._success_written = 0   # records written to current success part
        self._failed_part = 1
        self._failed_written = 0    # records written to current failed part

        # Override single-file paths set by parent with part-001 variants
        self.success_file = self._make_success_path()
        self.failed_file = self._make_failed_path()

    def _make_success_path(self) -> Path:
        return self.output_dir / f"scraped_records_{self.timestamp}_part{self._success_part:03d}.jsonl"

    def _make_failed_path(self) -> Path:
        return self.output_dir / f"failed_records_{self.timestamp}_part{self._failed_part:03d}.jsonl"
    
    def write_success(self, record: Dict[str, Any]) -> None:
        """Write a successfully scraped record (batched)."""
        with self._lock:
            self._success_batch.append(record)
            if len(self._success_batch) >= self.batch_size:
                self._flush_success()
    
    def write_failure(self, external_id: str, url: str, error: str,
                     entity_type: str = "unknown") -> None:
        """Write a failed scrape attempt (batched)."""
        record = {
            "external_id": external_id,
            "entity_type": entity_type,
            "url": url,
            "scrape_status": "FAILED",
            "error": error,
            "scraped_at": datetime.utcnow().isoformat() + "Z"
        }
        
        with self._lock:
            self._failed_batch.append(record)
            if len(self._failed_batch) >= self.batch_size:
                self._flush_failed()
    
    def _flush_success(self) -> None:
        """Flush success batch to file, rotating to a new part when the limit is reached."""
        if not self._success_batch:
            return

        try:
            remaining = self._success_batch[:]
            self._success_batch.clear()

            while remaining:
                # How many slots are left in the current part?
                slots = self.records_per_file - self._success_written
                chunk = remaining[:slots]
                remaining = remaining[slots:]

                with open(self.success_file, 'a', encoding='utf-8') as f:
                    for record in chunk:
                        json.dump(record, f, ensure_ascii=False)
                        f.write('\n')

                self._success_written += len(chunk)
                logger.info(f"Flushed {len(chunk)} success records → {self.success_file.name} "
                            f"({self._success_written}/{self.records_per_file})")

                # Roll to next part if the file is full
                if self._success_written >= self.records_per_file:
                    self._success_part += 1
                    self._success_written = 0
                    self.success_file = self._make_success_path()
                    logger.info(f"Rotated success output → {self.success_file.name}")

        except Exception as e:
            logger.error(f"Failed to flush success batch: {e}")

    def _flush_failed(self) -> None:
        """Flush failed batch to file, rotating to a new part when the limit is reached."""
        if not self._failed_batch:
            return

        try:
            remaining = self._failed_batch[:]
            self._failed_batch.clear()

            while remaining:
                slots = self.records_per_file - self._failed_written
                chunk = remaining[:slots]
                remaining = remaining[slots:]

                with open(self.failed_file, 'a', encoding='utf-8') as f:
                    for record in chunk:
                        json.dump(record, f, ensure_ascii=False)
                        f.write('\n')

                self._failed_written += len(chunk)

                if self._failed_written >= self.records_per_file:
                    self._failed_part += 1
                    self._failed_written = 0
                    self.failed_file = self._make_failed_path()
                    logger.info(f"Rotated failed output → {self.failed_file.name}")

            logger.info(f"Flushed failed records → {self.failed_file.name}")

        except Exception as e:
            logger.error(f"Failed to flush failed batch: {e}")
    
    def flush_all(self) -> None:
        """Flush all pending records."""
        with self._lock:
            self._flush_success()
            self._flush_failed()
        
        logger.info("All batches flushed")
    
    def __del__(self):
        """Ensure all records are flushed on destruction."""
        try:
            self.flush_all()
        except:
            pass
