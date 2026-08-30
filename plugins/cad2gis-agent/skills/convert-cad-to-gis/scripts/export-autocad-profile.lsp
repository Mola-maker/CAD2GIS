;;; CAD2GIS AutoCAD profile exporter.
;;;
;;; Load this file in the interactive AutoCAD session with APPLOAD, then run:
;;;   CAD2GIS_EXPORT_PROFILE
;;;
;;; The command reads the active profile and exports it to a new .arg file.
;;; It does not switch, import, reset, or modify profiles, and it never opens,
;;; saves, or changes a drawing.

(vl-load-com)

(defun cad2gis--ensure-arg-extension (path / extension)
  (setq extension (vl-filename-extension path))
  (if (and extension (= (strcase extension) ".ARG"))
    path
    (strcat path ".arg")
  )
)

(defun cad2gis--release-object (value)
  (if (and value (= (type value) 'VLA-OBJECT))
    (vl-catch-all-apply 'vlax-release-object (list value))
  )
)

(defun c:CAD2GIS_EXPORT_PROFILE
       (/ acad-object preferences profiles active-profile target-path result)
  (setq target-path
    (getfiled
      "Export the active AutoCAD profile for CAD2GIS"
      "cad2gis.arg"
      "arg"
      1
    )
  )

  (cond
    ((null target-path)
      (prompt "\nCAD2GIS_PROFILE_EXPORT_CANCELLED")
    )
    (T
      (setq target-path (cad2gis--ensure-arg-extension target-path))
      (if (findfile target-path)
        (progn
          (alert
            (strcat
              "CAD2GIS did not overwrite the existing file:\n"
              target-path
            )
          )
          (prompt
            (strcat "\nCAD2GIS_PROFILE_EXPORT_REFUSED\t" target-path)
          )
        )
        (progn
          (setq acad-object (vlax-get-acad-object))
          (setq preferences (vla-get-Preferences acad-object))
          (setq profiles (vla-get-Profiles preferences))
          (setq active-profile (vla-get-ActiveProfile profiles))
          (setq result
            (vl-catch-all-apply
              'vla-ExportProfile
              (list profiles active-profile target-path)
            )
          )
          (if (vl-catch-all-error-p result)
            (progn
              (alert
                (strcat
                  "AutoCAD could not export the active profile:\n"
                  (vl-catch-all-error-message result)
                )
              )
              (prompt
                (strcat
                  "\nCAD2GIS_PROFILE_EXPORT_FAILED\t"
                  (vl-catch-all-error-message result)
                )
              )
            )
            (progn
              (alert
                (strcat
                  "Exported AutoCAD profile '"
                  active-profile
                  "' to:\n"
                  target-path
                )
              )
              (prompt
                (strcat
                  "\nCAD2GIS_PROFILE_EXPORTED\t"
                  active-profile
                  "\t"
                  target-path
                )
              )
            )
          )
        )
      )
    )
  )

  (cad2gis--release-object profiles)
  (cad2gis--release-object preferences)
  (cad2gis--release-object acad-object)
  (princ)
)

(princ "\nCAD2GIS profile exporter loaded. Run CAD2GIS_EXPORT_PROFILE.")
(princ)
