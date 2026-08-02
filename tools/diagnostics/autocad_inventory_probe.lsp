;;; Read-only AutoCAD inventory complexity probe for robustness diagnostics.
;;; Output path is supplied through CAD2GIS_PROBE_OUTPUT.

(defun c2g-probe (/ output file selection blockdata blockname entity kind
                    drawingcount blockcount blockentitycount)
  (setq output (getenv "CAD2GIS_PROBE_OUTPUT"))
  (if (or (null output) (= output ""))
    (progn
      (princ "\nCAD2GIS_PROBE_OUTPUT is required.")
      nil)
    (progn
      (setq drawingcount 0 blockcount 0 blockentitycount 0)
      (setq selection (ssget "_X"))
      (if selection (setq drawingcount (sslength selection)))

      (setq blockdata (tblnext "BLOCK" T))
      (while blockdata
        (setq blockname (cdr (assoc 2 blockdata)))
        (if (and blockname
                 (/= (strcase blockname) "*MODEL_SPACE")
                 (not (wcmatch (strcase blockname) "*PAPER_SPACE*")))
          (progn
            (setq blockcount (1+ blockcount))
            (setq entity (entnext (tblobjname "BLOCK" blockname)))
            (while entity
              (setq kind (cdr (assoc 0 (entget entity))))
              (if (= kind "ENDBLK")
                (setq entity nil)
                (progn
                  (setq blockentitycount (1+ blockentitycount))
                  (setq entity (entnext entity)))))))
        (setq blockdata (tblnext "BLOCK")))

      (setq file (open output "w"))
      (write-line (strcat "drawing_selection_count\t" (itoa drawingcount)) file)
      (write-line (strcat "block_definition_count\t" (itoa blockcount)) file)
      (write-line
        (strcat "block_definition_entity_count\t" (itoa blockentitycount))
        file)
      (write-line
        (strcat "total_visited_count\t"
                (itoa (+ drawingcount blockcount blockentitycount)))
        file)
      (close file)
      (princ "\nCAD2GIS inventory probe complete.")
      T)))

(princ)
