import numpy as np
import re

class SemanticChunker:
    def __init__(self, model_name='BAAI/bge-m3', threshold_percentile=60, max_chunk_tokens=500):
        """
        A lightweight, framework-agnostic semantic chunker.
        Uses BGE-M3 embeddings to detect semantic drift between sentences.
        """
        # Imported here rather than at module scope so subclasses that supply
        # their own embedding backend (e.g. Ollama) do not need torch installed.
        from sentence_transformers import SentenceTransformer

        print(f"Loading {model_name}...")
        self.model = SentenceTransformer(model_name)
        self.threshold_percentile = threshold_percentile
        self.max_chunk_tokens = max_chunk_tokens  # Safety cap for chunk sizes

    def _split_into_sentences(self, text):
        """Splits text into sentences using simple regex-based boundaries."""
        # Regex targets periods, question marks, and exclamation marks followed by spaces or newlines
        sentence_end = re.compile(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s')
        sentences = [s.strip() for s in sentence_end.split(text) if s.strip()]
        return sentences

    def _calculate_cosine_distances(self, embeddings):
        """Computes cosine distances between consecutive sentence embeddings."""
        distances = []
        for i in range(len(embeddings) - 1):
            emb1 = embeddings[i]
            emb2 = embeddings[i+1]
            
            # Vector dot product over product of norms
            similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
            distance = 1 - similarity
            distances.append(distance)
        return distances

    def chunk_text(self, text):
        """Chunks text based on semantic distance spikes between sentences."""
        sentences = self._split_into_sentences(text)
        if not sentences:
            return []
        if len(sentences) == 1:
            return sentences

        # Generate dense embeddings using BGE-M3
        embeddings = self.model.encode(sentences, convert_to_numpy=True)
        
        # Calculate distances between consecutive sentences
        distances = self._calculate_cosine_distances(embeddings)
        
        # Determine the dynamic threshold based on percentiles
        breakpoint_threshold = np.percentile(distances, self.threshold_percentile)
        
        chunks = []
        current_chunk = [sentences[0]]
        
        # Approximate token count helper (1 word ~ 1.3 tokens for safe estimation)
        def approx_tokens(sentence_list):
            return sum(len(s.split()) * 1.3 for s in sentence_list)

        for i, distance in enumerate(distances):
            # Check if distance exceeds threshold or if the current chunk is getting too large
            if distance > breakpoint_threshold or approx_tokens(current_chunk) > self.max_chunk_tokens:
                chunks.append(" ".join(current_chunk))
                current_chunk = [sentences[i + 1]]
            else:
                current_chunk.append(sentences[i + 1])
                
        if current_chunk:
            chunks.append(" ".join(current_chunk))
            
        return chunks

# Example Usage
if __name__ == "__main__":
    chunker = SemanticChunker(threshold_percentile=65)
    
    sample_text = (
        "Semantic chunking splits text based on meaning. This approach ensures related context "
        "stays in the same node, which drastically improves RAG pipeline accuracy. Conversely, "
        "fixed-size chunking blindly slices strings by token counts, often cutting sentences in half. "
        "Let's switch topics completely. High-performance storage clusters require careful networking. "
        "Using isolated VLANs and RDMA fabrics ensures low latency and high throughput for heavy distributed file systems."
    )
    
    resulting_chunks = chunker.chunk_text(sample_text)
    for idx, chunk in enumerate(resulting_chunks):
        print(f"\n--- Chunk {idx+1} ---")
        print(chunk)
